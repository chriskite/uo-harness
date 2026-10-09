# UO Outlands — Anti-Cheat & Automation-Detection Research Report

Date: 2026-09-27
Client: `ClassicUO.exe` STANDARD_BUILD 1.0.2.544 (shipped 2026-09, Outlands fork of ClassicUO)
Launcher: `Outlands.exe` (patcher + OutlandsID login UI)
Scope: identify every mechanism that could detect or flag an external agent harness, so the harness evades them — on any shard.

---

## 1. Executive summary

- The Outlands client and launcher are **NativeAOT-compiled native binaries** (no .NET runtime, no IL). This is itself an anti-tamper/anti-tooling choice: ILSpy/dnSpy are useless; analysis requires native RE (Ghidra). It also makes runtime .NET injection (Harmony, Reflexil, etc.) impossible.
- Documented enforcement is primarily **server-side + human**: harvesting CAPTCHAs, GM responsiveness checks, AFK/unattended-play enforcement, server-driven Razor restrictions, client version restrictions.
- The client contains **account-security infrastructure** (OutlandsID, JWT claims, e-mail device verification). Its **device ID is a random GUID, not TPM-based**, stored in a disguised DPAPI blob, sent as the `X-Device-ID` HTTPS header and echoed in the game JWT `hash` claim (§4.2). It's aimed at ban-evasion and multi-account control, not at gameplay automation per se.
- The binary contains **Outlands-proprietary detection-category names** (`Speedhack`, `AutoClicking`, `AutoKeyboard`) and a **timing channel** (`Send_TimeSyncPingReq`) — see §6. The suspected "integrity channel" `Send_UOLive_HashResponse` turned out to be **upstream UltimaLive map-block CRC sync, not anti-cheat** (confirmed in ClassicUO source).
- Runtime capture (§7): a full login + 22 min of idle play uses **exactly two connections** (short-lived Cloudflare HTTPS auth; persistent game TCP :2593). **No beacons, telemetry, or side channels — any client-side detection reporting must ride inside the game protocol itself.** Process-enumeration and anti-debug APIs are **not imported** by the binary.
- The sanctioned assistant (Razor CE fork) itself uses keyboard hooks and `SendInput` — meaning *the client's own* synthetic input is expected; foreign synthetic input is the category enforcement targets.

## 2. Target architecture (verified)

| Component | Facts | Evidence |
|---|---|---|
| `Outlands.exe` (173 MB) | Launcher/patcher. NativeAOT. Contains AWS SigV4 signing strings (patch CDN), embedded web UI (patch panel / login). Args: `-installed`. Config: `Outlands.exe.json` (PatchSettings) | PE parse: no CLR header; `BSJB` @ `0x324455` |
| `ClassicUO.exe` (67 MB) | Game client. NativeAOT (no CLR header, `Rhp*` Redhawk symbols, single embedded `ReadyToRun` header, no `coreclr`/`hostfxr`/`runtimeconfig` strings). Static CRT; imports only OS DLLs. Sections: `.text` 25 MB code, `.rdata` 37 MB data/metadata | PE parse + string scan |
| Assistant | Razor Community Edition fork, compiled into the client (no plugin DLL on disk). Outlands-extended script engine (`findtype`, `findlayer`, gump expressions…). Profile dir `Data/Plugins/Assistant/` | install tree, [Razor Scripting wiki](https://wiki.uooutlands.com/Razor_Scripting) |
| Network | Game: `play.uooutlands.com:2593` (from `settings.json`). Auth: `https://login.uooutlands.com`. Login = OutlandsID → JWT (claims: `outlandsid`, `purpose`, `mahid`, `mahleader`) → game session | `settings.json`, strings |

Implication for tooling: **Ghidra (native) is the correct analysis tool. Any "client modification" approach (patches, DLL plugins, Harmony) is off the table** — technically (AOT) and because version/file checks make tampering detectable.

## 3. Documented detection & enforcement surface

What Outlands publicly documents about how automation is caught — the threat model the harness evades:

- **Client/tool policy**: only the Outlands Launcher and its built-in Razor assistant are sanctioned; every other tool, any artificial/automated input, and any programmatic data extraction (journal/memory reading, OCR, packet sniffing, memory scanning, event-reacting scripts) is treated as a bannable offense. Consequence for the harness: leave no client-side trace and make every emitted packet byte-identical to stock-client traffic.
- **In-client detection component**: Outlands states its detection operates "solely within the game client" (automated systems + administrative review). Consistent with §4: detection-category names exist in the client metadata, but no process-scanning/anti-debug imports.
- **Human review**: staff watch for AFK/unattended gathering; a player unresponsive to a GM for ~2 minutes while gathering is jailed, then banned on repeat. Consequence: staff-signature detection (`speech_guard`, the `gm_suspected` alarm) and human-plausible behavior everywhere.
- **Server side**: packet-timing and behavioral statistics (§8.3), server-driven Razor gating (kill-switch, PvP script restrictions), client/file version gating.

From the wiki:

- **Captcha**: harvesting (lumber/mining/fishing/forensics/shearing/lockpicking) prompts a CAPTCHA every 5–10 min; 3 fails = 6 h harvest block; persists across relog ([wiki](https://wiki.uooutlands.com/Captcha)). Client has `CAPTCHA_GUMP_ID` (the captcha is a normal gump; the client knows its ID — plausibly to stop Razor auto-answering).
- **2FA**: email-based new-device verification for OutlandsID ([news, Feb 2026](https://uooutlands.com/news/outlandsid-and-infrastructure-updates/)).
- GM AFK checks on harvesters are routine (public reports).

From the community (Discord capture `harness/data/discord.db`, 97k messages 2026-06-26 – 2026-10-04, and Jaseowns' public scripts; read 2026-10-04). Confidence M (player reports), H where it is the script's own code:

- **A scripted, multiboxed harvester was jailed.** A #harvesting regular wrote on 2026-08-08 13:00 UTC "i was triple boxing but my one of mine just arrested and jailed / now im double boxing" ([msg](https://discord.com/channels/1520125994659348631/1520906212319695108/1535633732654997514)). His other messages say he multiboxed 2 LJ + 1 PvM (2026-07-11) and used "jaseown's miner" (2026-08-12); which script ran on the jailed character isn't stated. No reply and no detail: what staff said, and how long he took to answer, are unknown. Players describe the check the same way: "mining lumberjack can't do afk bc if staff drop in bye bye if you don't talk" and "If staff pop in and your script is still going they will still put a timer on you … 2nd time is a ban" (Ian, 2026-08-08 / 2026-09-15). **No message quotes a GM's in-game words.**
- **Jaseowns' "Ultimate Mining Script" ([script](https://outlands.uorazorscripts.com/script/69b83c73-6b39-4afd-9be0-539b037967cb), [history](https://github.com/jaseowns/uo_outlands_razor_scripts/commits/main/outlands.uorazorscripts.com/script/69b83c73-6b39-4afd-9be0-539b037967cb)) has no defence against a staff check.** It reads only system messages (`insysmsg`), never speech, so a GM talking to it changes nothing: it keeps sending "Use item in hand" ~0.2 s after each harvest reply, forever (`replay`). Its only escape is Tracking hunting "all hostile players": any "now tracking" line recalls and stops the script. Staff aren't hostile players, so they don't trigger it [INFERENCE]. Before 2024-04-19 a run of ~7.5 s without a known harvest message counted as a captcha: it waited 20 s and stopped. The [commit](https://github.com/jaseowns/uo_outlands_razor_scripts/commit/508b0a545b2b08a8f8177e5a05288f1b50ec8f1e) replaced that with a hint and an endless retry (Discord, M: the captcha force-stops all Razor scripts anyway).
- **His lumber scripts are the same design, with less.** "Ultimate Lumberjack Script" ([script](https://outlands.uorazorscripts.com/script/7832ffda-9637-454b-84f1-823be2c05048); the 2025-06-02 commit only reworded its stop messages), "Simplified Lumberjacking to avoid thieves" ([script](https://outlands.uorazorscripts.com/script/c99c2326-5d06-4c9b-8200-d9ed56e01574)) and "ULTIMATE SIMPLEST JACKING SCRIPT" ([script](https://outlands.uorazorscripts.com/script/9d673679-9649-4b90-9513-649005b7b85c)) all send "Use item in hand", wait for the cursor, then "Target Self" (Smart Harvest), and read only system messages. The Ultimate one recalls on "now tracking" but doesn't set Tracking up itself (the player must have hunting on); after ~7.5 s without a known harvest message it waits 20 s for "Captcha successful", then stops. The thieves variant has no player escape at all. The simplest is 9 lines: use, target self, wait 500 ms, repeat forever, no reading of anything. None reads speech, and none handles the Stationary Penalty except by printing "Move to next spot" for the human.
- **Razor has no scripted walking on Outlands since ~2023-07.** The script says "Walking has been removed. So Ive updated the script 7/10/2023", and its guide page "Walk has been removed from Outlands". The wiki's [Razor Scripting](https://wiki.uooutlands.com/Razor_Scripting) page has no walk command. So the sanctioned assistant harvests in place and a human moves to the next spot (the script prints "Move to next spot"; players bind a sound to it). A harvester that also walks between spots on its own is beyond what Razor can do [INFERENCE: staff may read that as third-party tooling]. Our runners walk between trees, which keeps this a standing reason for human-plausible movement (§8.3) and the speech hold.

## 4. Client-side findings — static analysis (strings → confidence-rated)

Source: full ASCII+UTF-16 string extraction of both binaries (373k client strings), categorized scans. String offsets are file offsets in `ClassicUO.exe`.

### 4.1 Automation-detection indicators

| Finding | Strings (offset) | Assessment |
|---|---|---|
| Detection-category enum | `Speedhack` (0x1be01f7), `AutoClicking` (0x1be0201), `AutoKeyboard` (0x1be020e) — adjacent in the metadata name heap | **High** confidence these are members of an Outlands enum (e.g. violation/detection type). Owner + usage: pending Ghidra xref (§6). |
| Server hash challenge | `Send_UOLive_HashResponse` (0x19e36da) | **Resolved (§6): upstream UltimaLive map-block CRC sync (packet 0x3F), not an integrity/AC channel.** |
| Timing channel | `Send_TimeSyncPingReq` (0x19e37a1) | Time-sync ping; standard input to server-side speedhack/timing heuristics (compares client clock vs packet arrival). |
| Razor kill-switch | `IsRazorBlockedSysMessage` (0x1a3327a) | Server can disable Razor functions via sysmessage flag. |
| Razor PvP gating | `EnablePvpScriptRestrictions` (0x1bbe937), `IsPvpFlagged`, `DisableWhenPvpFlagged`, `<CheckPvp>b__20_0/21_0` | Server flags a player PvP → Razor scripts restricted. Automation is actively constrained in PvP contexts. |
| Synthetic-input APIs | `SendInput`, `SetWindowsHookEx`, `GetAsyncKeyState`, `BlockInput`, `CheckGlobalKeys`, `ResetKeyboardUsingSendInput` | Attributed (by adjacent names) to Razor's hotkey/macro engine and WinForms — the *sanctioned* assistant synthesizes input. Not evidence of AC scanning, but proves input synthesis happens in-process. |
| Process/window enumeration | `K32EnumProcesses`, `K32EnumProcessModulesEx`, `EnumWindows`, `GetWindowTextW`, `GetClassNameW` | Present in binary. **Ambiguous**: may belong to `System.Diagnostics.Process`/WinForms plumbing. Requires xref attribution (§6) before claiming the client scans for tools. |
| Screenshots | `TakeScreenshot`, `CaptureScreen`, `CaptureScreenToFile`, `GenerateSnapShotWithBitBlt`, `_lastSelfDeathScreenshot`, `ScreenshotStoredIn`, `screenshotTab` | Razor CE screenshot feature + Outlands death-recap. No evidence (yet) of server-requested screenshot upload; no upload model found in API surface. |
| Anti-debug | `IsDebuggerPresent` | Also present in any .NET app via `System.Diagnostics.Debugger`; weak signal. Xref attribution pending. |

### 4.2 Account-security infrastructure (ban-evasion control, not gameplay AC)

- JWT identity: `CLAIM_NAMESPACE = http://uooutlands.com/identity/claims`; claims `outlandsid`, `purpose`, `mahid` (multi-account household), `mahleader`.
- Device binding (**resolved 2026-09-30 by Ghidra decompile of the 544 binary plus a runtime check; full report [`docs/research/DEVICE_ID_RE.md`](docs/research/DEVICE_ID_RE.md), script `ghidra_scripts/DeviceIdRE.java`**):
  - **No TPM use.** `TPMManufacturer`/`TPMModel`/`TPMVersion` are .NET `OidLookup` friendly names (OIDs 2.23.133.2.1–3, `FUN_140a24120`). `Microsoft Platform Crypto Provider` is only the `CngProvider` getter. The NCrypt imports are only Import/OpenKey/OpenStorageProvider/Get/SetProperty/FreeObject/DeleteKey; there is no CreatePersistedKey, SignHash or Tbsi. [VERIFIED]
  - **The device ID is a random GUID** (`LoginScene.GetDevice @0x1402d57c0` → `Guid.NewGuid`), with no hardware input. It is stored as base64 DPAPI (LocalMachine, entropy `04 08 0F 10 17 2A`) in **`%APPDATA%\FNA3D\D3D11_Shader_Cache.blob`, a file disguised as a shader cache**. On this machine it was created 2026-09-27. If the file is missing or unreadable, the client makes a new GUID. [VERIFIED decompile + runtime decrypt]
  - **It goes over HTTPS** in the `X-Device-ID` header on every `login.uooutlands.com` request (`CreateHttpClient @0x1402d4370`). Beside it goes `X-Request-Context` = the first 8 bytes, as hex, of HMAC-SHA256 (static key) over `{ver.Major}{ver.Revision}{deviceGuid}`, which binds the ID to the client build. [VERIFIED; purpose INFERENCE]
  - **It goes over the game TCP** only indirectly. The login server signs it into the game JWT as the `…/claims/hash` claim, and the client forwards that JWT in `0x91`. The JWT's `…/claims/userdata` holds an IPv4 address, presumably the client IP the server saw [INFERENCE]. The client writes no device field into any game packet itself. Runtime: the `hash` claim had one value across all 32 captured sessions (544 and 550, both server IPs) and equals the decrypted local GUID. [VERIFIED]
  - Other machine IDs are not sent. GetAdaptersAddresses is used only for proxy setup, MachineName only for the upstream settings.json password key, and the Windows user name only in unused Razor code. The binary has no WMI, volume-serial or MachineGuid use. [VERIFIED xrefs; send-path absence INFERENCE where noted in the report]
  - Harness implication: the proxy forwards `0x91` unchanged, so the server sees the stock device ID. Rule 5 (no DeviceId spoofing) stands. Deleting or copying the blob changes or clones the device identity; never touch it.
- Version enforcement: `get_VersionRestrictionsLoaded`, `get_IsMostRecentVersion`, `get_IsMostRecentGameFilesVersion`, `VersionRestrictions`, `ServerClientRestriction`, `GetMd5`/`CompareMd5`. Client/launcher verify client + game-file versions; outdated or modified files block login.
- Ban UX: `Banned`, `banneduntil`, `BanMessage`, *"Your account is under review or has been suspended due to a violation of the Code of Conduct…"* (0x1ebba88).

### 4.3 What was NOT found

- No Sentry/Bugsnag/AppCenter/crashlytics; no telemetry hostnames. Only: `login.uooutlands.com`, `portal.uooutlands.com`, `discord.uooutlands.com`, `uooutlands.com`, `classicuo.eu`.
- No strings indicating memory scanning of foreign processes, no named-pipe/ETW telemetry channels, no obvious beacon/heartbeat model names in the HTTPS API surface (`…Model` inventory: login, device verify, privacy policy, coins, link, client info — nothing periodic).
- No EasyUO-style signature scanning strings (tool names like `cheatengine`, `autohotkey.exe` etc. absent).

## 5. Network endpoint inventory (static)

Client binary hardcodes only: `https://login.uooutlands.com` (auth), links to `portal.uooutlands.com/vendor-search`, `discord.uooutlands.com`, `uooutlands.com`, `classicuo.eu`. Game server address arrives via config/login response. Launcher additionally carries AWS SigV4 signing (patch CDN). Runtime verification in §7.

## 6. Ghidra attribution (native xref analysis)

Method: full headless analysis of the 67 MB binary (Ghidra 12.1.4, project `ghidra/UOProject`), `ACXrefs.java` (raw byte search + reference walk for every §4.1 string), cross-checked against upstream **ClassicUO `main`** and **Razor CE `master`** source trees (`ClassicUO-main/`, `Razor-master/`).

Two structural facts frame everything:

- All §4.1 ASCII names resolve to **exactly one location each in the NativeAOT metadata heap with zero code xrefs** — AOT code references metadata by index, so static string-xref attribution is impossible by design. (UTF-16 runtime literals remain code-referenced; sweep below.)
- The Win32 API name strings split cleanly by **import-table (IAT) references**:

| API string | IAT xrefs | Verdict |
|---|---|---|
| `GetAsyncKeyState`, `SetWindowsHookEx`, `EnumWindows` | yes (real imports) | Assistant (Razor) hotkey/window plumbing — sanctioned code |
| `K32EnumProcesses`, `K32EnumProcessModulesEx`, `IsDebuggerPresent` | **none — not imported** | dead strings in a runtime name table; the client does **not** statically bind process-enumeration or anti-debug APIs. Residual dynamic-resolution possibility noted, no supporting evidence |

UTF-16 literal sweep (full binary): the only Outlands-specific AC-adjacent literals are `VerifyingAccount`, `YouHaveBeenBanned`, and the suspension message. **No user-facing "automation/speedhack detected" warning strings exist** — consistent with silent/server-side enforcement rather than client-side warnings.

Item-by-item attribution:

| Item | Attribution | Confidence |
|---|---|---|
| `Send_UOLive_HashResponse` | **Upstream ClassicUO UltimaLive** (`Game/UltimaLive.cs`, `Network/OutgoingPackets.cs:4510`): outgoing packet `0x3F` answering server subcommand `0xFF` with CRC16 checksums of the 25 map blocks around the player, so the server pushes stale map/statics updates. **Map-data sync, not a client integrity scan** (consistent with the Feb 2026 world-data infrastructure update). | Confirmed (source) |
| `Speedhack` / `AutoClicking` / `AutoKeyboard` | In **neither** upstream ClassicUO nor Razor CE → Outlands-proprietary. Metadata-only (adjacent enum-member names in heap). No user-facing warning literals exist. Most plausible: violation categories of a server-report or internal gating enum. **Statically unattributable → watch on the wire.** | High (existence), inference (purpose) |
| `Send_TimeSyncPingReq` | Outlands-proprietary (not upstream). Name + heap neighbors imply a client-time sync request — classic input to server-side speedhack/timing heuristics (server compares client timestamps vs packet arrival). Treat as the timing-detection channel: a proxy must forward these unmodified and unjittered. | Inference (strong) |
| `CAPTCHA_GUMP_ID`, `IsRazorBlockedSysMessage`, `EnablePvpScriptRestrictions`, `IsPvpFlagged`, `DisableWhenPvpFlagged` | Outlands-proprietary server→client **assistant gating**: captcha gump identification (anti auto-answer), Razor kill-switch, PvP script restrictions. Automation constraints are server-driven and visible in-band. | High |
| `GetMd5` / `CompareMd5` | Metadata-only; heap neighbors are map/asset-loading names (`ReportInvalidMapIDs`, `FileIndex`, …). Consistent with asset/version verification (`VersionRestrictions`, `IsMostRecentGameFilesVersion`) at login, not runtime AC scanning. | Medium |
| `TakeScreenshot`, `CaptureScreenToFile`, `GenerateSnapShotWithBitBlt`, `_lastSelfDeathScreenshot` | Razor screenshot tab + Outlands death-recap feature. **No upload model exists in the HTTPS API surface**, and the behavioral capture (§7) showed no image-sized uploads — screenshots are local-only. | High |
| `TPMModel`, `get_DeviceId`, `VerifyDeviceModel`, `ServerClientRestriction`, `VersionRestrictions`, `IsMostRecentGameFilesVersion` | Outlands account/version enforcement (OutlandsID, device verification, household claims, client-version gating). Purpose: account-sharing / multi-boxing / ban-evasion control at **login time**, not gameplay automation. **`TPMModel` is a .NET OID name, not TPM use; DeviceId = random GUID in a disguised DPAPI blob, sent as `X-Device-ID` and echoed in the game JWT `hash` claim (§4.2).** | Confirmed (decompile + runtime) |

## 7. Behavioral verification (runtime)

**Method**: 30-minute endpoint monitor (`monitor_endpoints.ps1`, 2 s sampling of the OS TCP table for `Outlands.exe`/`ClassicUO.exe`) covering: launcher idle, two login attempts (one via VPN exit node, one direct), and 22 minutes of logged-in idle play on the Test Shard (Shelter Island).

**Raw capture** (`behavioral_endpoints.csv`):

| Window | Endpoint | State | Interpretation |
|---|---|---|---|
| 20:39–20:41 | `104.26.0.46:443` | Established, ~2 min | HTTPS auth/API via Cloudflare — login attempt 1 |
| 20:40:23 | `74.91.115.123:2593` | **SynSent, never Established** | game-server SYN dropped — VPN exit node filtered at network layer |
| 20:45–20:46 | `104.26.0.46:443` | Established, ~75 s | HTTPS auth — login attempt 2 (direct) |
| 20:45–21:07 | `74.91.115.123:2593` | Established, ~22 min | game session (login → idle in Shelter) |

**Findings**:

1. **The client's entire runtime network surface is two connections**: one short-lived HTTPS auth per login (Cloudflare-fronted) and one persistent game TCP. Across 22 minutes of idle play it opened **zero** additional connections — no periodic beacons, no telemetry hosts, no crash reporting, no screenshot uploads. **Any client-side anti-cheat reporting must travel inside the game protocol on port 2593 itself.**
2. **Active network-layer IP filtering** sits in front of the game host (74.91.115.123, NFOservers): SYNs from a VPN exit node were dropped (not RST), while Cloudflare HTTPS accepted them. Filtering is per-destination-IP/port at the host or its edge firewall, applied equally to manual play.
3. **The launcher makes no outbound connections while idle** at its UI (10-minute observation) — no phone-home on the patcher side either when no patch is needed.
4. Auth happens over Cloudflare HTTPS and completes in ~75 s including 2FA-free device-recognized login; the game session does not re-contact the auth API afterwards.

## 8. Evasion design for the agent harness

Working rules, each tied to a detection surface above:

1. **Never touch the client process.** No injection, no patching, no window subclassing, no synthetic input into its message loop. (NativeAOT plus version/file checks: tampering is both hard and detectable.)
2. **Network-position observation is the lowest-touch surface**: a localhost TCP proxy between client and `play.uooutlands.com:2593` leaves the client 100% stock. Residual risk: server-side packet-timing/behavioral heuristics.
3. **Assume all automation-detection is server-side statistics + human review**: movement timing, action inter-arrival times, 24/7 uptime, perfect play, CAPTCHA response latency. The harness adds human-like jitter and sessions of human length; captchas are answered by the human at the PC by default, or by the solver with human-plausible latency when the viz toggle says `auto` (§8.8).
4. **Respect Razor gating signals** (`IsRazorBlockedSysMessage`, PvP restrictions): when the server restricts assistants, the harness halts automated actions.
5. **Device/account infrastructure is out of scope**: do not attempt to spoof `DeviceId`/TPM/2FA; log in through the official launcher normally.
6. **Keep files stock**: `VersionRestrictions`/`IsMostRecentGameFilesVersion` means modified game files may block login outright; the harness never writes into the install dir (work in the repo checkout).
9. **Walk-train "gate" (observed 2026-09-29; superseded the same day, see below: it was the client's own walker, not the server).** First reading at the time: the server detects movement trains without interleaved client activity; ~10+ uninterrupted injected walks were rejected wholesale, and after repeated solo trains even the human's arrow keys stopped working. The rule drawn from it (bursts ≤ ~6 steps, interleaved with client activity) is **not implemented**: runners walk whole routes (see the status note after the S2C evidence).
   **Reinterpretation (2026-09-29, sessions 20260929_142237 / _143051 / _144541):** the arrow-key lockout was client-side, with no server penalty involved. The server's ConfirmWalk for walks the client didn't send trips the client's bad-step path (`WalkingFailed = true`, latched single resync), and the client stays frozen until a server walker reset. The server apparently ignores resyncs < ~5 s apart, so fast agent steps outran the reset (docs/MOVEMENT.md). **Live-supported:** with agent steps spaced ≥ 5 s, 6/6 stepped and the user's own arrow keys kept working. The "trains rejected wholesale" observation is likely the same mechanism plus ladder drift, not a behavioral gate.
   **S2C evidence (2026-09-29, corrected decode, docs/CIPHER.md §4):** in session 142237 the server *confirmed* all 12 agent continuation walks (`22 01..0c 01`) of a solo train while the frozen client drew nothing. **No server-side rejection of walk trains exists.** The lockout was 100% client-side. Server-side *passive* behavioral analysis can't be ruled out from the wire, so the harness keeps agent movement at the stock client's own pace: the proxy enforces 0.2 s run / 0.4 s walk minimum step spacing and at most 5 unconfirmed walks (the client's `MAX_STEP_COUNT`), and the Mover steps at the held-key cadence (§8.14).
   **Status 2026-09-30:** runners walk whole routes as agent-only trains (e.g. 6 028 agent walks in 20260930_123206, none rejected as a train); no burst cap exists in code. The residual is passive statistics only.
10. **Spent-token re-presentation — closed by the proxy (2026-09-29).** When the proxy stamps the cycle token into an injected opener, the client still holds its own copy and would present it on its next walk — a spent token re-presented, which a stock client never does and a server log could flag. `MoveAuthority` tracks that copy (`stale_token`) and zeroes it once, so the server sees exactly one presentation per token. Other client keys on continuations pass through because genuine mid-cycle server pushes exist (docs/MOVEMENT.md). The resync-per-external-walk cadence of the fix-A era is gone since fix B (§8.11): the audited sessions show client resyncs only after late walk confirms, closed by §10 A2.
11. **Fix B detection surfaces (2026-09-29; revised same day by user decision).** (a) The proxy drops/rewrites S2C `0x22` ConfirmWalk packets toward the client, and re-anchors the client with a **fabricated S2C `0x21` DenyWalk**. Both are client-side only and invisible to the server. The client code has no integrity check on the S2C stream beyond decoding. (b) **The proxy sends no packets of its own to the server.** The earlier design's proxy-originated resync after each agent burst was removed, because it was a pattern a stock client never produces. Server-visible C2S is now exactly the client's own traffic plus the agent's walks/actions (seq/key rewritten to be consistent). (c) Agent step pacing is enforced proxy-side (Speedhack category). Residual server-visible surface: agent walks themselves (timing and paths), and the absence of the client resyncs that fix A produced.
    **Addendum 2026-09-30:** a second client-only fabrication. After the agent answers a gump
    (`0xB1`), the proxy sends the client S2C `0xBF` sub 4 (close generic gump, button 0) for that
    gump id, so the client stops drawing a gump the server already closed. With button 0 the
    client only disposes it and sends nothing back (ClassicUO PacketHandlers.cs:4154-4183). The
    server still sees exactly what a player's client sends: one `0xB1`.
8. **CAPTCHA strategy — auto-solve built, human-solved by default (see the 2026-10-01 decision below).** The captcha is the shard's dedicated automation tripwire (3 fails = 6 h harvest block; response latency and long-term accuracy are trivially usable as detection statistics), so whoever answers must be accurate and take human-plausible time. No OCR is needed for auto-solve: the captcha arrives as a normal gump, the harness detects it from the gump-open packet (gump id + entry/button structure — never text, §8.13's decoys), and the digits are drawn as `tilepic` dot glyphs at layout coordinates, so they are read straight from the layout (template matching against known glyph graphics; a screen-crop classifier or small CNN is the fallback if glyph graphics rotate). Auto policy: detect → read digits → answer via the text-entry + submit-button packets after a human-plausible delay (measured human solves: 7.7–17.5 s) → resume. Accuracy must leave the 3-fail budget with wide margin; a low-confidence read falls back to the human rather than guessing. Human policy (the default): pause + beep + wait for a solve observed in the client.

    **Implemented offline (2026-09-30, `harness/captcha.py`).** The solver splits the layout's tilepics into the three digit clusters at the two largest x gaps, normalizes each cluster (centroid-centered, y-span scaled), and matches it against a reference set mined from the 7 captured captchas (`harness/data/captcha_font.json`; 21 labeled digit samples + a synthetic oval-ring 0 [INFERENCE, no captured 0 yet]) with a translation-aligned trimmed Chamfer distance. Acceptance is margin-gated: a digit counts only if its nearest reference beats every other digit's nearest by ≥ 9 %; otherwise the runner falls back to pause + beep instead of guessing (a wrong answer is a strike). Measured offline (`harness/test_captcha.py`): all 7 captured captchas solve to their accepted answers with the random submit button picked out; jitter bootstrap (σ≈3.5 px, 15 % dot dropout, 0-2 noise dots) gives 99.2 % correct among accepted digits at 12 % rejection. The runner answers with the stock `0xB1` (all text entries, `actions.gump_reply`) after a human-plausible delay (lognormal median 11.5 s; measured human solves 7.7–17.5 s), tolerates `--captcha-max-strikes` rejections (each rejection re-opens a fresh captcha), then falls back. Not yet run live.

    **Live (session 20261001_191355, 97 min lumber run, Shelter).** 4 real captchas, all accepted ("Captcha successful."): the first (637) answered by the human in the client, the next three (482, 311, 373) by the solver in `auto`, 10.8, 10.8 and 17.4 s after the gump opened (human: 10.8 s). Every agent `0xB1` went to gump id 1 with its random submit button (1004, 604, 506); 0 replies to the 465 decoys. The four were out of sample for the font, and their digit margins show how thin the 22-reference font was: 0.195 for the 3 of 637, and **0.109/0.111 for both 3s of 373**, just above the 0.09 gate (digit 3 had one reference). A leave-one-session-out pass over all 11 captchas is right for 10/10 whose digits have a reference from another session. It **confidently misreads 194 as 184 (margin 0.154)** once the only real 9 is held out: the margin gate guards ambiguity, not a digit shape the font has never seen. So a 0 (synthetic reference only) or an unusual 9 can still be a confident wrong answer, i.e. a strike. The 12 new digits were added to the font (34 references; `harness/test_captcha.py` §5 runs the held-out check), which drops the in-sample noise bootstrap from 99.2 % to 97.9 % correct among accepted digits (17.6 % rejected).

    **Second real 9 (session 20261001_214649, 2026-10-01 22:10, answered by the human: "911", submit 528, accepted).** The user called it "real funky": the 9's 14 dots spread 86 px wide (the earlier 9 about 45), with strays on both sides of the loop, and the last 1 has a stray dot far right of its stem, so that cluster is 86 px wide too. The solver (font of the 11 earlier captchas, one real 9) read it right out of sample: 911 at margins 0.636/0.717/0.647, submit button 528 picked out. Added as the 12th sample (37 references). With two real 9s, the leave-one-session-out pass now covers all 12 captchas and solves all 12; 194's 9 reads right from the new 9 alone (margin 0.502). Noise bootstrap: 98.1 % right among accepted digits, 16.4 % rejected. Digit 0 still has only the synthetic reference.

    **Dataset 24 (2026-10-03).** 12 more real captchas, all accepted: 11 answered live by the solver in `auto` on 2026-10-02 (sessions 20261002_153718 ×6, 181221 ×1, 183845 ×4: 978, 881, 571, 331, 551, 422, 114, 772, 356, 841, 182; agent `0xB1` 7.1–20.2 s after the gump opened) and one by the human on 2026-10-03 (150103: 357, 7.5 s). All 12 were out of sample for the 37-reference font, and every digit's margin was ≥ 0.25 (lowest: the 5 of 357 at 0.250, the 5 of 356 at 0.272, the 9 of 978 at 0.284). Added as samples 13–24 (73 references; per digit 1:15, 2:8, 3:9, 4:6, 5:7, 6:4, 7:12, 8:8, 9:3, 0 still synthetic). Leave-one-session-out now covers and solves 24/24, closest digit margin 0.247 (the 3 of 373, 0.109 before). The synthetic noise bootstrap dropped slightly: 97.2 % right among accepted digits, 19.4 % rejected (98.1 % / 16.4 % with 37 references). The real glyph variation is wider than the bootstrap's jitter, so more real references put other digits' shapes closer to a jittered one [INFERENCE]; on real captchas the font has never answered wrong. `captcha._dist` was rewritten to compute one squared-distance matrix per shift for both Chamfer directions (same result to 3e-17, ~1.7× faster; ~0.19 s per digit against 73 references).

    **Dataset 26 (2026-10-03, first `harness/captcha_mine.py` run).** Session 20261003_150103 kept recording after the hand mining and held two more accepted captchas: 437 (submit 606) and 792 (submit 933); the 73-reference font read both right out of sample (min margins 0.581 / 0.427). Now 26 samples, 79 references. Leave-one-session-out: 26/26 solved, closest margin 0.284 (the 9 of 978). The synthetic noise bootstrap reads 96.98 % right / 19.1 % rejected, just under the old 97 % bar; since every real reference added lowers that synthetic figure while the real-captcha evidence improves, the test bar is now 96 % (decision 2026-10-03, this session).

    **Split fix (2026-10-06).** In the last two days the solver answered 35 captchas and refused one: 175 (session 20261005_194043, 14:01, Seer7 run 15), which the human then answered (149 s, accepted). The digits were fine; the split wasn't. A stray dot of the 5 sat 29 px right of the rest, the same width as the gap between the 1 and the 7, so "cut at the two largest x gaps" cut off the stray dot, and the one-dot cluster failed the sanity gates (None, so the fallback and not a wrong answer). `captcha.digit_clusters` now takes the pair of gaps that leaves three plausible clusters (6-25 dots, height 40-120, width ≤ 120), widest by its narrower gap. It solves 175, and all 20 other real captchas of that session give the same answers as before. Fixture: `harness/testdata/captcha_split.json` (`test_captcha.py` "stray dot").

    **Dataset 55 (2026-10-06).** The session jsonl keeps only the first 64 bytes of each packet, but `captcha_mine.py` replays the full `.s2c.raw` capture, so sessions 20261005_194043 (27) and 20261006_164724 (2) were mined: 29 accepted captchas. Out of sample for the 79-reference font: 27 read right and 2 refused, 0 wrong. The refusals were 482 (16:42, "8" vs "9", margin 0.037) and 458 (17:31, the "4", margin 0.036); both were human fallbacks that day. Now 55 samples, 166 references. The synthetic noise bootstrap reads 96.55 % right and 25.5 % rejected, so the rejection bar went 25 → 30 % (the same drift as the 2026-10-03 accuracy bar). Leave-one-session-out: every held-out capture is right or refused.

    **Captcha mode: human by default, auto by toggle (user decision 2026-10-01).** Who answers is a memory-store setting (`meta.captcha_mode`, `Memory.captcha_mode()`), switched by the `captcha [human|auto]` toggle in the viz header (docs/VISUALIZER.md §2.2a). Unset means `human`: the runner pauses, posts an urgent `captcha` juncture and beeps until the solve is seen in the client, so nothing is sent for the captcha. In `auto` the solver above answers; unreadable layouts, rejected answers, or the client answering first fall back to the human wait. The runner reads the mode at every captcha and on every poll while it waits, so flipping to `auto` mid-wait hands the newest captcha to the solver. Before sending, the solver checks that the client hasn't already answered that captcha (no `0xB1` for its serial and no "Captcha successful."), so one gump never gets two answers. The overseer (`ctl act gump`) never answers the captcha in either mode. Smoke-tested offline against a scripted link: default human, auto, a mid-wait flip after a wrong human answer (the solver answered the newest captcha), and the client answering during the solver's delay (nothing sent).

12. **Injected speech must be keyword-encoded like the stock client (2026-09-29).** The stock client encodes any speech that matches a `speech.mul` keyword (type |= 0xC0, 12-bit ids, UTF-8). The Outlands encoder `Send_UnicodeSpeechRequest @ 0x140151c20`, `GetKeywords @ 0x1401bbc60` and `IsMatch @ 0x1401bba40` are the upstream algorithm. The harness's old `say_unicode` always sent plain UTF-16. So the **"hello" injected during the Phase 3 live test (session 20260928_211622) was not client-identical**: speech.mul id 59 = "hello", and a stock client would have sent it encoded. Server-side, a keyword word arriving unencoded is a detectable anomaly [INFERENCE on whether it is checked]. Fixed: `harness/uo/speech.py` + `actions.say_unicode` now reproduce the stock client exactly (verified against the real client's "bank" `ad0016c0…62616e6b00`, session 20260929_161433). Likewise, "look at NPC" now sends the stock sequence `09` + `34 …04` (+ `98` for unnamed), as seen in 518/523 real clicks.

13. **Decoy "Captcha" gumps: a honeypot for text-matching bots (2026-09-29, session 20260929_204225; structure confirmed, purpose [INFERENCE, high]).** Every lumberjacking attempt opens a gump that contains the captcha's words ("Captcha", "Type the Value", "Click when complete"). Each one has:
    - a fresh random gump id (≥ 10 distinct in one session)
    - `nomove/noclose/nodispose`
    - **no reply button**
    - its text as `croppedtext` at negative (offscreen) coordinates
    - `xmfhtmlgump` entries with cliloc numbers that don't exist in Cliloc.enu

    The human sees nothing, and the stock client can't answer it. Any gump response for one of these ids can only come from automation, so a response is a near-certain detection signal. The **real** captcha is gump id `0x00000001`: a `textentrylimited` (id 2, max 3 chars) plus reply button 594, with the digits drawn as `tilepic` dot glyphs (graphics 572/6255) at layout coordinates. The human's answer was `b1 … button 594, text entry 2 = "326"` → "Captcha successful."

    Harness rules:
    - Captcha detection keys on the gump id plus the entry/button structure, **never on text and never on a fixed button id**.

    **Update (live 2026-09-29, session 20260929_220932): the submit button id is random per captcha.** The demo's captcha used 594; the next one used **843**. Its dot glyphs were also different graphics (11695 and 2457). Guide button 1 and text entry 2 were the same both times. The first detector required button 594, so it missed the second captcha. There was no handoff and no beep, and the user solved it unprompted. Detection now requires gump id 1, text entry 2 and a reply button other than Guide 1, and the offline e2e uses a submit id other than 594. [INFERENCE] The randomisation targets bots that replay a fixed button id.
    - The agent never sends a gump response for a gump that offers no reply button.

    The digits are machine-readable from the layout, so auto-solving needs no OCR (§8.8). The decoys show the server is actively set up to catch naive automation.

14. **Behavioural texture (2026-09-29, user request).** Mitigation for the §8.3 statistics surface. Every agent runner draws its timing and route choices from `harness/humanize.py`:
    - lognormal reaction times per action kind, with fatigue drift
    - per-plan route noise at the scale of 6×6-tile map cells instead of the one optimal path, with zig-zag stretches regrouped into straight runs (`nav.straighten`), pauses and sidesteps. Routes are always run: the user's client has Always Run on, so a walked route would be the odd one out (user, 2026-10-01; walked routes were 7 % before)
    - steps at the stock client's held-key cadence (200 ms run / 400 ms walk plus frame jitter, measured from the previous send); the pauses sit between stretches, not inside them
    - doors opened like the client's auto-open (`PlayerMobile.TryOpenDoors`): the open-door request goes out right after the turn or step that faces a door on the next tile, before stepping into it. Routes never cut diagonally past a door, and every step is re-checked against the objects the client has right then (§10, fixed 2026-10-01)
    - occasional cursor hesitation (stock Esc cancel). **Not in the harvest cycle since 2026-10-04 (user decision):** chopping runs at a Razor lumber script's pace, the hatchet ~0.2 s after each harvest reply and the cursor answered with ourselves ~0.1 s after it comes, no fidgets (removed). Rationale: the shard's sanctioned assistant runs scripts that do exactly this (Jaseowns' lumber and mining scripts: "Use item in hand" ~0.2 s after each reply, then "Target Self", §3), so a person-paced chopper is the outlier, not a script-paced one [INFERENCE: what staff look at is unknown]. Measured before the change: 9.4 s median per chop cycle against the server's 4.2 s from target to reply (session 20261004_220218).

    Constraints: only stock-identical packets or waiting. It never beats the proxy's pacing floor or the gate. No free-text speech (PLAN.md speech allowlist). [INFERENCE] Whether Outlands' server models these statistics is unknown. The texture is cheap insurance, not a guarantee.

    **Correction 2026-09-30 (§10 A5, A8).** The first version also "missed turns" on purpose and ran into a map-known obstacle. The stock client never sends that walk (`PlayerMobile.Walk` returns false when `Pathfinder.CanWalk` fails, PlayerMobile.cs:572-575), so it was removed, and `ctl act walk` refuses a step the map says can't be walked. The earlier lognormal step rhythm (~0.40 s median on the wire, none at 200-220 ms) and per-step route noise (37 % heading changes) didn't look like a held key either. Measured offline after the change (not yet live): the pacing loop with a simulated 60 ms confirm sends steps 203-215 ms apart, and 24 Shelter routes planned with the new noise have ~20 % heading changes (human: 200-220 ms, 21 %).

15. **Only act on what a player could see from where they stand (2026-09-29, found by the user
    watching live run 3).** The server accepted harvesting surface trees (z 5) and talking to the
    innkeeper from a cave 25–41 z below (its range checks look 2D `[INFERENCE]`). A human there
    couldn't see or click those targets, so the action pattern is impossible for a real client
    and would stand out to a GM or a log review. Rule: every target goal is height-aware:
    - harvest targets need the standing body to overlap the target vertically
    - NPC interactions happen within one storey of the NPC

    The proxy computes z per confirmed step from the map (NOTES.md), so goals and planning use
    the true height. Tests: `harness/test_pathfind.py` (real map) and `harness/test_mover.py`.

16. **Seeing the game window: passive capture only (2026-09-30, user request).** `harness/screen.py`
    uses Windows Graphics Capture: DWM hands over the window's composited frames, the way OBS
    "Window Capture" does.
    - Nothing goes to the client: no injection, no PrintWindow (that sends WM_PRINT into the
      client), no WindowFromPoint (hit-test messages).
    - The window is found by caption via EnumWindows/GetWindowTextW. For another process's
      window, GetWindowTextW reads the stored caption without WM_GETTEXT.
    - No capture border, no cursor.
    - The client has no screen-capture detection we know of: no process-scan imports, and no
      reporting channel outside the game connection (§4, §7).
    - Verified live on the elevated ClassicUO window while it was fully covered.
    - Screen *input* stays forbidden (§8.1). The screenshot is for looking.
    - **Addendum 2026-09-30:** the viz Live view (`harness/liveview.py`) uses the same capture
      continuously, at up to 10 fps, and only while someone watches. It is still passive:
      nothing is sent to the client, and there's no border or cursor. OBS-style window capture
      is ordinary for streamers, so it isn't an automation signal either.

17. **Combat: monsters yes, players never (2026-09-30, user decision).** The overseer may fight
    hostile monsters, loot their corpses, heal itself (spells, potions, bandages) and buy supplies.
    - PvP stays off: Heat of Battle (recall and inn room blocked) and the criminal flag on
      blue/green targets make it mechanically costly, and fighting players is the fastest route
      to staff attention.
    - It's enforced in `ctl`, not left to the model:
      - `attack` and `target` accept only mobiles `threats.identify` calls monsters, with
        notoriety 3–6. Blue/green are players' pets, and attacking them is a criminal act.
      - `loot` refuses human corpses.
      - `buy` is capped by `policy.json`.
    - Every action uses the stock client's packet sequence (war mode then attack; context menu
      Buy then 0x3B; cast 0xFF sub 4 then 0x6C), so the server sees what a player's client sends.

18. **Target cursors the agent answered (2026-09-30, §10 A1).** When the agent answers a server
    target cursor (`0x6C`) the client still shows, the proxy hands the client the server's own
    cancel shape (`6c 00 00000000 03` + padding) so the cursor goes away, and drops the client's
    answer to that cancel (a `0x6C` cancel carrying the spent cursor id; ClassicUO
    TargetManager.SetTargeting → CancelTarget). Before, a later server cancel made the client
    answer a cursor the agent had already answered: a second reply to one cursor, which a stock
    client can't produce (4× in 20260930_123206). This is the only client packet the proxy ever
    drops, and only for a cursor id it saw the agent answer while the client showed it; a new
    server cursor with that id clears it. Test: `test_movement.py` e2e.

19. **Reading the official websites (2026-10-08, user request; docs/PLAN.md "Wiki, patch notes and
    forums in the knowledge base").** `harness/outlands_web.py` reads wiki.uooutlands.com (MediaWiki
    API), uooutlands.com news (WordPress REST API) and forums.uooutlands.com (public HTML pages,
    logged out) from this computer's home IP, the one the game account plays from.
    - What the sites see: anonymous GETs with a desktop Edge User-Agent, no cookies or login, paced
      1–2 s (wiki/news) and 1.5–3.5 s (forum) per request. A full first crawl is ~2 100 wiki API
      calls and a few thousand forum pages over a few hours; later runs fetch only what changed.
    - Exposure [INFERENCE]: none in-game; the requests carry no OutlandsID, JWT or game data, and
      the game server never sees them. A web admin could link a steady anonymous reader to the
      household IP through their web logs. Reading the wiki and forums is what players do, but
      a first crawl's volume is above one person's reading. Mitigation: the pacing above, no
      parallel requests per host, and incremental re-runs (no scheduled crawler).
    - Nothing is posted; the tool has no code path that writes to a site.

## 9. Open questions

- ~~What exactly does `Send_UOLive_HashResponse` hash?~~ **Answered: map-block CRC16s for UltimaLive world sync (upstream source).**
- ~~Any periodic connections beyond login/game during a session?~~ **Answered: none (30-min capture, §7).**
- ~~Does the client scan processes / use anti-debug?~~ **Answered: those APIs are not imported (no IAT entries).**
- Is there a client→server packet carrying the `Speedhack/AutoClicking/AutoKeyboard` categories (server-report), or are they internal? → resolve by wire-watching through the harness proxy; note packet IDs here when seen.
- When does `Send_TimeSyncPingReq` fire and what timestamps does it carry? → same method; proxy must pass it through unaltered.
- ~~Is `TPMModel` actual TPM attestation?~~ **No** (2026-09-30, §4.2): it's an OID friendly name; the device ID is a random GUID.

## 10. Detection-surface audit (2026-09-30)

Code + docs + all tagged session logs (11 fully aligned sessions 20260929_163420 … 20260930_123206:
63 441 C2S, of which 10 503 agent). Wire claims are measured; server-side *use* of any of them is
[INFERENCE].

**Clean on the wire:** keepalive/TimeSync never injected, dropped or delayed (median gap 1.054–1.075 s
with or without agent packets in the gap; RTT 47–63 ms); 0 C2S framing desyncs; every agent packet
matches the client's own layout; 0 seq gaps; agent deny rate 2.5 % vs human 2.0 %; agent step rate
never above the stock throttle; 0 agent packets referencing serials the server never sent; 0 agent 0x6C
without a server cursor; 0 replies to 127 decoy captchas or to the 6 real ones (human answered in
7.7–17.5 s); 0 server messages about macro/AFK/GM/jail/Razor across 4 126 decoded messages.

**Server-visible deviations from a stock client:**

| # | Deviation | Evidence | Fix direction |
|---|---|---|---|
| A1 | Client answers an agent-consumed target cursor again: after the agent's 0x6C, the client's cursor stays up, and a later server cancel makes it send a 0x6C for the same cursor id. A stock client can't reply to one cursor twice. | 4× in 20260930_123206 (ln 28303, 57061, 76114, 96710); proxy.py has no 0x6C handling (cf. the 0xB1 close at proxy.py:320-325) | Track agent-answered cursor ids; drop the client's later 0x6C for them |
| A2 | Late walk confirms (2.0–2.3 s) arrive after `CONFIRM_TIMEOUT_S` = 1.5 s, get forwarded to the client (`on_confirm` → "forward" for an unknown seq) and cause client bad-step resyncs `22 0000`. Also one re-sent seq 109. Human sessions: 0 resyncs. | 123206 ln 33028/33037, 59106/59112, 91403; 091704 ln 17256 | Tombstone expired agent seqs and still hide their confirms; raise the timeout |
| A3 | Context-menu request with no 0x09 precursor. The stock right-click sends `09 <serial>` then `bf 0013` (DelayedObjectClickManager.cs:34-44). | Human 30/30 with 0x09, agent 0/15 (ctl.py:799-804, `_act_buy`, errand_bank) | Send `single_click` first |
| A4 | Interaction with out-of-view entities: `ctl npcs`/`menu`/`dclick`/`menu_pick` accept any serial with no range, freshness or open-menu check. | `bf0013` to Zara the scribe 33 tiles away (091704 ln 13344) | Require in-view (≤ 18), fresh position; `menu_pick` only from an open 0xBF 0x14 |
| A5 | Walk into a map-known obstacle ("bump" texture, humanize §8.14). The stock client never sends it: `PlayerMobile.Walk` returns false when `Pathfinder.CanWalk` fails (PlayerMobile.cs:572-575). Doors: 78/78 agent OpenDoor came after a 0x21 deny, while stock `TryOpenDoors` opens before stepping. | agent_link.py:344-356, 438-442, 304-311 | Drop static bumps (keep mobile shoves); open doors before the step |
| A6 | 0xB1 without the gump's text entries: the stock client always sends every entry (Gump.cs:136-167). | 5 agent replies in 123206 (ln 34741-35571), one drew "That is not a valid number." Fixed in ctl since ln 37271; loop_lumber.py:524, 576 still bypass `gump_reply_fields` (room gump has no entries today) | Route every 0xB1 through `gump_reply_fields` |
| A7 | Stock companion packets missing: `single_click` without the 0x34 the client pairs with it (97-99 % in captures); attack without 0x34; `dclick` on a mobile in war mode (stock sends 0x34+0x05 instead, GameActions.cs:299-309); `warmode` sends a non-flipping 0x72. | ctl.py:799-804, 897-937 | Mirror the stock shapes |
| A8 | Movement texture: run-flagged steps at a lognormal ~0.40 s (0 intervals in 180-220 ms) where a held run key gives quantized 0.200 s; 37 % of agent walk packets are heading changes (staircase paths) vs 21 % human. | 9 505 agent intervals; human baseline 204225 | Stock 0.2 s cadence within segments, humanize between segments; smooth paths |
| A9 | No GM/staff handling in the runners: nothing reacts to staff speech, notoriety-7 humans are classed `npc` (threats.py:264), unsolicited teleports just replan; loop_lumber lacks errand_bank's `GATING_WORDS` guard (rule 4). A break mid-walk aborts (agent_link.py:218-232, ~200 s bound) and strands the character in the field. | code | Halt + alert on staff signatures, teleports and restriction messages in every runner; break at a safe place |
| A10 | Session shape: 20260930_123206 was 4.6 h connected with 6 377 agent C2S and essentially no human input except 2 captchas. Captcha cadence is the only attendance check. | log counts | Human-length sessions |

**Status (2026-09-30, same day):** A1–A8 fixed in code, offline-tested; not yet run live.

| # | Fix | Test |
|---|---|---|
| A1 | client-only cancel + drop of the client's reply for the spent cursor (§8.18; `SessionTap.release_client_cursor`) | `test_movement.py` e2e |
| A2 | `CONFIRM_TIMEOUT_S` 3.0 s; expired walks stay recognizable for 5 s (`MoveAuthority.late`): a late confirm is hidden and moves the ladder past its seq; agent walks wait out that window instead of re-sending the seq; agent walks also stop at 5 unconfirmed (stock `MAX_STEP_COUNT`) | `test_movement.py` unit |
| A3 | `menu` and `buy` send `09` then `bf 0013` back to back (`ctl.context_menu_packets`) | `harness/test_ctl.py` |
| A4 | `dclick`/`single_click`/`menu`/`menu_pick`/`attack` need the entity (or the mobile/ground item holding it) in the world model within 18 tiles (`ctl.not_clickable`); `menu_pick` needs that serial's context menu open with that index | `harness/test_ctl.py` |
| A5 | bump texture removed; doors opened ahead (§8.14); `ctl act walk` never sends a step the map rules refuse | `harness/test_mover.py` |
| A6 | every 0xB1 is built by `actions.gump_reply` from the gump's layout (ctl and the lumber room menu) | `harness/test_actions.py` |
| A7 | `single_click` on a mobile adds its `0x34`; `attack` sends `0x34` unless the client has an outstanding status request for the mob (corrected 2026-10-01: not "hits unknown", see §10 A7); `dclick` on a mobile in war mode is refused; `warmode` sends nothing when already in that state | `harness/test_ctl.py` |
| A8 | stock held-key cadence, cell-scale route noise, `nav.straighten` (§8.14) | `harness/test_nav.py` + offline route measurement |

A9 and A10 remain open, except two parts of A9: since 2026-10-01 a due break gives a 10-minute
grace and wakes the overseer (`break_due` juncture), which can head home and `ctl break`
(agent_gate.py, docs/OVERSEER.md); and on harvest jobs a character speaking nearby holds the job
for the overseer (`speech_guard.py`, `speech_nearby` juncture: all-clear, or talk to a possible GM;
PLAN.md). Still open: staff detection itself (no Outlands staff seen yet), unsolicited teleports,
and `loop_lumber`'s missing gating-text guard.

**Live verification (session 20260930_182751, 18:28-19:04 local):** an overseer lumber trip at
Horseshoe Bay, a recall to Prevalia with a hatchet bought from a provisioner, and renting a room
after the midnight wipe. The whole capture was replayed through `SessionTap` offline (0 timer
divergences) and every agent packet was checked against the world state at that moment.

| # | Verdict | Evidence |
|---|---|---|
| A1 | **verified** | 4 agent target answers, each to the active cursor; 4 client-only cancels, each answered by the client 2-6 ms later and dropped; 0 client `0x6C` relayed |
| A2 | **verified** | 0 client resyncs; 0 duplicate or out-of-order walk seqs on the wire (714 walks, all agent). One walk confirmed 6.8 s late, during the midnight-UTC wipe freeze: it was hidden, the ladder moved on and nothing was re-sent |
| A3 | **verified** (1 sample) | `09 000028bb` → `bf 0009 0013 000028bb` 0 ms apart; menu pick 0.9 s later, `0x3B` 1.1 s after that |
| A4 | **verified** | 11 agent serial references, each at ≤ 4 tiles or in the pack |
| A5 | **partly** at the time; both gaps fixed 2026-10-01 (below) | doors: 7 requests, 55-112 ms after the walk that faced the door (human 55-101 ms), 6 followed by a step. 2 denies: the diagonal past a closed double-door leaf, and a step into a barrel (below) |
| A6 | **verified** | 11/11 agent `0xB1` byte-equal to `actions.gump_reply` for the gump's layout |
| A7 | **verified live 2026-10-01** (same session, combat test) | Attack 1 (13 tiles): `72 01` → server `72 01` → 0.55 s later `34 … 000f88ff` and `05 000f88ff`, 0 ms apart (stock Tab, then double-click: RequestMobileStatus + Attack). Attack 2 (adjacent, 46.7 s later): `34` + `05` again. That is stock-correct: the client's own `09`/`34`/`bf 000c` bursts for the mob had left no status request outstanding (the original "hits unknown" rule was wrong; corrected to the client's status-request state, commit e095bb3). `warmode off` after the death sent nothing: the server had already turned war mode off. Single-click on a mobile and the war-mode double-click refusal weren't exercised. |
| A8 | **verified** for run | 693 run steps: gaps p10/median/p90 207/213/218 ms (min 203); 21 % heading changes. No walked route was drawn, so the 400 ms walk cadence is unverified |

**A11 (found 2026-10-01, high; fixed the same day, verified live in session 20260930_201411): the client's display lags the character during agent walks,
and its own packets show it.** Agent walk confirms are hidden from the client, and the client is
re-anchored only after 0.5 s without a walk. In session 182751 that left it standing where each
walking stretch began: stretches ran up to 102 steps / 21.7 s, and the display lagged the true
position by 10 tiles (median), 31 (p90) and 53 (max). The client still reacts to every mobile the
server sends near the true position: `09` + `34` (0 of 186 were > 18 tiles from the true
position). But 173 of 186 were > 18 tiles from where the client displays itself, so
`World.Update` drops them at once and `SendCloseStatus` sends `bf 000c`. Click → close within
10 ms: **93 % during agent walking vs 5 % and 1 % in human sessions** (median close delay 0.00 s
vs 7.7-7.9 s). That's a per-mobile signature a server log sees directly. Side effects: after a
long stretch the client lacks objects the server believes it sent, and `ctl`'s on-screen check
(server truth) can pass for an entity the client no longer has. Fix direction: re-anchor the client
(the client-only `0x21`) after every confirmed agent step once nothing is in flight, instead of
after 0.5 s of quiet, so the display trails by at most a tile. Then re-measure the click → close
delays.

**A11 fix:** the proxy now hands the client the re-anchor in place of every hidden agent confirm
(nothing else in flight), so the display follows the character tile by tile (`SessionTap.reanchor_client(on_confirm=True)`;
the 0.5 s timer stays for rejections and late confirms). A consequence: with Auto Open Doors on
(the user's client had it: 3 client door opens 55-101 ms after human walks in 204225),
`DenyWalk` → `SetInWorldTile`/direction change would fire the client's own `TryOpenDoors` as well
as the Mover's request, and two requests toggle the door shut again. **User decision
2026-10-01: Auto Open Doors is turned off in the client; the Mover alone opens doors** (a human
who clicks or uses a hotkey to open doors sends the same single request). A first version that
waited 150 ms for the client's request and sent its own only if none came was dropped. If the
setting comes back on, the door-deny retry still gets the character through (one extra request).
Test: `test_movement.py` e2e (one re-anchor per confirmed agent step).

**Live verification (session 20260930_201411, overseer walk room → Horseshoe Bay town → room,
Auto Open Doors off):** 237 agent steps, all run packets, 0 blocked; every hidden confirm got its
on-confirm re-anchor (237/237). Client `34` type 4 → `bf 000c` close delays during agent walking:
**0 of 10 within 10 ms, median 9.9 s** (before: 173/189 = 92 %, median 0.00 s; human sessions
7.7–7.9 s). The 2 door opens came from the agent only: the client sent no open-door request, no
walk and no resync. Small sample (the town's 3 NPCs plus passers-by); re-check on a longer walk
with more mobiles.

Unchanged: keepalive median gap 1.057 s, p99 1.087 s (2 075 keepalives); C2S senders only client
and agent; 0 server messages about macro/AFK/jail/Razor/automation in 266 decoded messages.

**New findings from that run (fixed 2026-10-01, offline-tested):**
- **Per-step map check missing in `Mover`.** It checked walkability when it planned, never again
  before each step. A barrel (`0x0E77`, impassable) arrived 1 s after the route was planned and
  the Mover walked into it 6 s later ((2003,2227) S, denied). The stock client runs CanWalk on
  every step against the objects it has at that moment and would have sent nothing. **Fix:**
  `Mover.step_walkable` re-checks each step with the current ground items; a refused step isn't
  sent and the route is replanned (`refused_steps`). `ctl act walk` re-checks every step the same
  way. Test: `harness/test_mover.py` (object lands on the route after the plan).
- **Diagonal past a closed door.** The planner treated doors as passable, and the auto-open only
  covers the facing tile. So a diagonal step past a closed door on a corner tile was sent and
  denied ((2027,2218) NW). The stock client counts door items (closed or open) as impassable and
  wouldn't send that step. **Fix:** `pathfind.Walk.can_walk` refuses a diagonal with a door on
  either corner tile (`door_corners`; the proxy's z tracking of confirmed steps passes False). A
  step onto the door's own tile stays plannable, since the door is opened ahead. Test:
  `harness/test_pathfind.py`, with the live door state.

**Closed containers (found 2026-10-01 in the gap review, fixed the same day; verified live in
session 20260930_201411).** The agent lifted, used and targeted items in containers the client
had never opened, and dropped into containers whose gump was never open. A stock client can only
reach an item through an open container gump, and opening one is a C2S `0x06` the server answers
with `0x24`, so the server can see "took from a container it never showed this client". In the
demo capture (204225) the human opened containers before taking from them (e.g. `06 45756183` →
`0x24` → `07 45756184` 2.6 s later). **Fix:** `agent_link.containers_to_open` (from the
world model's `0x24` set) plus `Link.open_containers` and `ctl._open_first`: outermost first, a
stock double-click and a wait for the `0x24`, then a lognormal "find" reaction (median 1.2 s).
Used by every ctl act that reaches into a container and by `loop_lumber` (backpack before
targeting logs, secure box before storing). A closed bank box is refused (only `bank` speech opens
it); `target` into a closed bag is refused (with a cursor up a click targets). Tests:
`harness/test_ctl.py` (outermost first, opened once, closed bank refused), `test_loop_lumber.py`
(backpack then box, each once). **Live (201411):** a freshly bought bag (never opened) as the
destination of `drop <dagger> <bag>`: `06 45ce64a1` → server `24 45ce64a1` 70 ms later → `07`
dagger 1.0 s after that → `08` into the bag; reported `opened: [bag]`. Moving it back re-opened
nothing. Equip/unequip with the client-opened backpack re-opened nothing either. Limit: lifting a
worn item (`unequip`) needs the paperdoll open in a stock client; the harness doesn't open it
(not a container, not addressed).

**Exception: potions and the trapped pouch by serial, like Razor (2026-10-07, user decision).** The
lumber runner's flight aid and between-chop self care (`healing.in_pack`, LUMBER_LOOP.md §13) double-
click a potion or a live trapped pouch anywhere in the backpack by serial, without opening its bag
first. That is what the assistant built into the Outlands client does. Razor CE `potion "heal"`
(`PlayerData.UseItem`) and `findtype … backpack` + `dclick` (`Item.FindItemsById(recurse: true)`)
walk the client's known pack tree and send one `0x06` for the item, with no `0x06` for its bag (Razor
CE `master`, Scripts/Commands.cs `Potion`, Core/Player.cs `UseItem`, read 2026-10-07). The user's own
heal script does it. The harness's world model only holds items the server sent this client, so
the server has shown these items to it. Every other reach into a container (lift, drop, target,
`ctl act use`) still opens the container first.

**Live audit (session 20261001_191355, 2026-10-01, 97 min, lumber runner on Shelter, captcha mode
`auto`).** Replayed offline through `viz_feed.ReplayDriver`: exact interleave, every jsonl row
matched its raw packet (20 744 C2S, 83 929 S2C), 0 parse failures, 0 world-model anomalies. The
packet-id and sub-id inventory (0xBF and 0xFF subs, both directions) holds **nothing absent from
every earlier capture**. Unparsed but known: S2C 0x54 sound, 0xC0 effects, 0xAF death anim (5),
0xBF/0xFF subs per docs/WORLDMODEL.md, and the per-login 0xF0/0xC8/0xBC/0x55/0x5B/0xB9.
- **Senders:** C2S only client (15 067) and agent (5 677); proxy-originated packets only to the
  client (5 150 = 4 668 re-anchors + 479 cursor cancels + 3 captcha gump closes).
- **A1:** 479 agent target answers, each to a live server cursor; 479 client-only cancels, each
  answered by the client and dropped; 0 client `0x6C` relayed. 13 of the agent `0x6C` are the
  humanize hesitation cancel (`6c 01 <cid> 00 … 7fffffff×3`): byte 1 = the cursor's target type
  and cursor type 0, which is what upstream `TargetManager.CancelTarget` sends on Esc. The
  dropped client replies read `6c 00 <cid> 03` because they answer the proxy's
  `6c 00 00000000 03` cancel (SetTargeting with type 0/Cancel), not a user Esc.
- **A2 under world saves:** 4 666 walks, all agent, 0 consecutive duplicate seqs, 0 client
  resyncs. Both `walk_rejected` events were world saves (server pause 3.0/3.1 s): the confirm came
  3.14/3.17 s after the step, just past `CONFIRM_TIMEOUT_S` 3.0; it was hidden, the ladder moved on,
  nothing was re-sent.
- **A11 at scale:** client `34` → `bf 000c` within 10 ms: 6 of 2 072 during agent walking (0.3 %,
  median 8.8 s), 17 of 300 otherwise (median 9.3 s). Before the fix: 92 %, median 0.00 s.
- **Keepalive:** 5 403 TimeSync requests, all client; median gap 1.082 s, p99 1.199, max 2.906
  (earlier sessions 1.054–1.075 median, p99 1.087). The 11 gaps over 1.5 s are client-side: the
  server reply came in 54 ms–1.4 s and the client's next request followed 1.0–2.3 s after it. 4 of
  the 11 had no agent packet near them. Whether the proxy's forwarding adds to the client's delay
  isn't measurable from proxy-side timestamps [INFERENCE: client frame hitches].
- **Messages:** 1 070 system messages; the only keyword hits are the 4 "Captcha successful." and a
  player achievement name. No GM, jail, macro, AFK or Razor text.
- **Renounce prompt:** one, from a route step onto a player-cast moongate on Shelter (NOTES.md);
  not answered.
- **Texture notes (not stock deviations on the wire):** after the second world-save rejection the
  replanned route turned back (W → N) before stepping; and the renounce gump stayed open in the
  client for the rest of the session, where a person would close it. **Fixed the same day:** the
  Mover now closes the gump of any moongate a route only passes over, with the stock `0xB1`
  button 0 after a reaction pause (docs/NOTES.md "Moongate gumps"; `test_loop_lumber.py` proves it
  through the real proxy, which also closes the client's copy). Not yet run live.

**A12 (found live 2026-10-01, session 20261001_214649; model fixed 2026-10-01): agent combat
packets at mobiles the client no longer had.** In the overseer's NPD mongbat fights, ctl's
on-screen guard passed because the world model kept dead and out-of-range mobiles (docs/NOTES.md
"World model keeps dead and out-of-range mobiles"). On the wire (timed replay, seconds after
1790911000):
- `0x002C1E27`: the client dropped it out of range at 176.6 (its own close-status `bf 000c`); it
  died out of our sight (corpse `0xDEAD` at 491.7). The agent sent `05` attack at 507.1 and 548.2
  and 3 `6C` Lightning targets at 856–895.
- `0x002C3593`: dropped by the client at our teleport (906.46), dead by our return (corpse
  `0xDEAD` at 1139.2). The agent sent one `05` (1156.1) and 9 `6C` (1160–1235).

The server answered "That is too far away." A stock client can't target a mobile it has removed
(dead, or beyond its view range), so these are agent-only shapes a server log can see. The
same gap let `dclick`/`menu`/`attack`/`target` reach any such ghost.

**Fix (2026-10-01):** the world model prunes like the client (docs/WORLDMODEL.md §7: the
decompiled `World.ProcessDeletes` range rule at the server's `0xC8` range, 18; deaths via
`0xAF`/`0xDEAD`; facet changes), so every guard that reads `world.mobiles`/`items` now refuses
these serials. The re-audit rule is `harness/audit_ghost_targets.py` (packets at serials the
client had dropped, distinct from "serials the server never sent"). **Numbers:** session
20261001_214649: agent 3 × `05` + 12 × `6C` flagged, all at the two mongbats (the earlier count of
2 × `05` missed the attack at 507.1); 0 client packets at a pruned serial (94 client `09`s at
mobiles the server created and deleted in the same burst are the client's queued auto-queries).
Session 20261001_191355: 3 agent `09` single clicks at players 139–822 s after the client had
dropped them. They were the idle fidget "look at a nearby mobile" (`humanize.Human.fidget`, which
picks from `world.mobiles` within 10 tiles). The lumber log for that run has "(idle: looking at
0x002994DA)" at 19:59:16, which matches flagged packet 2721.43 s. The fidget reads the pruned
live table now, so the fix covers it with no change to the fidget itself. Also
20260930_091704: the known context-menu request to a vendor out of view. Not yet run live (needs
a proxy restart).

**Other gaps:** the JWT carries no shard claim, and the NAT diverts every server IP on :2593 (since
2026-09-30, after logins went to 35.71.142.123 and 52.223.17.219 rather than the Test Shard's
74.91.115.123). So nothing in the harness distinguishes which shard a session is on, and which
shard those two IPs serve is unconfirmed; production may well be among them. The installed
client was patched to 1.0.2.550 on 2026-09-28 (JWT `version`), while the RE
behind `actions.py` is from 1.0.2.544. Captured 550 traffic frames cleanly, but nothing guards against
a future layout change. README.md and INTERCEPTION.md no longer claim a byte-identical relay
(corrected 2026-09-30).

**A13 (observed 2026-10-02, session 20261002_153718; fixed the same day, verified live in session 20261002_181221): the agent rode at the on-foot pace.**
Since the character mounted (S2C `0x2E` layer 0x19, 15:44), 3195 agent run steps went out at a
0.200 s minimum / 0.220 s median gap, all confirmed. The stock client steps every 0.1 s when it runs
mounted (ClassicUO MovementSpeed.cs). This is no Speedhack risk, because the agent is slower than
allowed. It is a behavioural difference [INFERENCE: visible to a GM watching, or in server-side
per-step timing, as hours of 5 tiles/s with the run flag on a horse]. A mounted cadence needs the
server's mount state in the proxy floor, plus a capture of the client riding to confirm Outlands
accepts 0.1 s steps. **Fix:** the user rode the client at 17:55. Its 56 mounted run steps had a
median gap of 0.100 s (min 0.083), and all were confirmed. The proxy floor and the agent cadence
now follow `StateStore.mounted()` (0.1 / 0.2 s mounted; docs/MOVEMENT.md). Residual risk: if the
world model kept a mount the server had removed, the agent would step at 0.1 s on foot and trip the
Speedhack check. The model drops the mount item on the server's 0x1D, the way the stock client
does; a missed dismount would mislead the client in the same way. **Live (181221, mounted lumber
trip at Terran, 18:13–18:15):** 104 agent steps, 73 same-direction run gaps of min 0.104, p10 0.106,
median 0.111, p90 0.117 s (the user's own: median 0.100, p90 0.104). All 104 confirmed (median
0.060 s), with no deny, no rejection and no client resync, and a re-anchor on every confirm.
Player houses (0xF3 multis) were also missing from the walk
rules. That cost 12 server denies at one house in this session (a denied-walk pattern no client
produces). Fixed the same day (docs/NOTES.md).

**A14 (found 2026-10-03 in the capture audit, sessions 20261003_113952/123614/125556; resolved by user decision 2026-10-03: Auto Open Corpses off in the client): the client's Auto Open Corpses follows every agent step, like Auto Open Doors did (A11).**
The user's client has Auto Open Corpses on (stock option; every double-click was within 2 tiles).
The Outlands client retries a corpse it couldn't open (upstream ClassicUO `TryOpenCorpses` adds
each serial to `AutoOpenedCorpses` and never tries it again; here single corpses got up to 42
tries, and the community KB says Outlands' auto-open "catches it on your next step",
docs/research/DISCORD_KB.md). The per-confirm re-anchor (fabricated `0x21`) is a position update
for the client, so during agent walking the client double-clicks every unopened corpse in range on
every agent step. In 125556 the client sent 262 double-clicks to 92 corpses (one corpse 42 times,
in bursts of 5 at 0.2–0.45 s [INFERENCE: agent walk-offs]); 195 of them came within 0.5 s
after a re-anchor with nothing in between. The server answered 212 × "You may not loot this
corpse." over the three captures. Server view: a player walking those steps himself with the
option on would send the same double-clicks [INFERENCE, medium: per the KB's retry-on-step]. So no
non-stock packet, but
the client spends actions the agent's loot may collide with (none seen: both 500119 "You must wait
to perform another action" of 10-03 followed human clicks). **User decision 2026-10-03: Auto Open
Corpses is turned off in the client; the agent opens its own corpses.** Related, agent side: the
hunt runner tried to lift from 35 corpses the server had just refused ("Players cannot commit
aggressive actions in that location.", docs/NOTES.md "Traffic audit"), each lift ~0.4 s after the
refusal; a human might try the same once, the runner does it every time [INFERENCE, low]. Looting
a blue (notoriety 1) corpse is a criminal act the server blocks (user, 2026-10-03), and every
refused corpse was blue in its `0xDEAD`, so the runner can skip them before walking over.

---

### Appendix A — Evidence artifacts (local)

Paths are relative to the repo checkout (laptop).

| Artifact | Path |
|---|---|
| Full client strings (373k) | `strings.txt` |
| Categorized scan hits | `grep_hits.txt` |
| API surface names | `api_surface.txt` |
| Endpoint inventory | `endpoints.txt` |
| Launcher strings/hits | `launcher_strings.txt`, `launcher_hits.txt` |
| Ghidra project + script | `ghidra\`, `ghidra_scripts\ACXrefs.java` |
| Behavioral monitor | `monitor_endpoints.ps1` |

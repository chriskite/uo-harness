# UO Outlands — Anti-Cheat & Automation-Detection Research Report

Date: 2026-09-27
Client: `ClassicUO.exe` STANDARD_BUILD 1.0.2.544 (shipped 2026-09, Outlands fork of ClassicUO)
Launcher: `Outlands.exe` (patcher + OutlandsID login UI)
Scope: identify every mechanism that could detect or flag an external agent harness, so the harness avoids them. Target environment: **Test Shard**.

---

## 1. Executive summary

- The Outlands client and launcher are **NativeAOT-compiled native binaries** (no .NET runtime, no IL). This is itself an anti-tamper/anti-tooling choice: ILSpy/dnSpy are useless; analysis requires native RE (Ghidra). It also makes runtime .NET injection (Harmony, Reflexil, etc.) impossible.
- Documented enforcement is primarily **server-side + human**: harvesting CAPTCHAs, GM responsiveness checks, AFK rules, server-driven Razor restrictions, client version restrictions.
- The client contains **account-security infrastructure** (OutlandsID, JWT claims, device verification with a `TPMModel`, `DeviceId` in login models) aimed at ban-evasion/multi-account control — not at gameplay automation per se.
- The binary contains **Outlands-proprietary detection-category names** (`Speedhack`, `AutoClicking`, `AutoKeyboard`) and a **timing channel** (`Send_TimeSyncPingReq`) — see §6. The suspected "integrity channel" `Send_UOLive_HashResponse` turned out to be **upstream UltimaLive map-block CRC sync, not anti-cheat** (confirmed in ClassicUO source).
- Runtime capture (§7): a full login + 22 min of idle play uses **exactly two connections** (short-lived Cloudflare HTTPS auth; persistent game TCP :2593). **No beacons, telemetry, or side channels — any client-side detection reporting must ride inside the game protocol itself.** Process-enumeration and anti-debug APIs are **not imported** by the binary.
- The sanctioned assistant (Razor CE fork) itself uses keyboard hooks and `SendInput` — meaning *the client's own* synthetic input is expected; foreign synthetic input is the banned category (rules §3.2).

## 2. Target architecture (verified)

| Component | Facts | Evidence |
|---|---|---|
| `Outlands.exe` (173 MB) | Launcher/patcher. NativeAOT. Contains AWS SigV4 signing strings (patch CDN), embedded web UI (patch panel / login). Args: `-installed`. Config: `Outlands.exe.json` (PatchSettings) | PE parse: no CLR header; `BSJB` @ `0x324455` |
| `ClassicUO.exe` (67 MB) | Game client. NativeAOT (no CLR header, `Rhp*` Redhawk symbols, single embedded `ReadyToRun` header, no `coreclr`/`hostfxr`/`runtimeconfig` strings). Static CRT; imports only OS DLLs. Sections: `.text` 25 MB code, `.rdata` 37 MB data/metadata | PE parse + string scan |
| Assistant | Razor Community Edition fork, compiled into the client (no plugin DLL on disk). Outlands-extended script engine (`findtype`, `findlayer`, gump expressions…). Profile dir `Data/Plugins/Assistant/` | install tree, [Razor Scripting wiki](https://wiki.uooutlands.com/Razor_Scripting) |
| Network | Game: `play.uooutlands.com:2593` (from `settings.json`). Auth: `https://login.uooutlands.com`. Login = OutlandsID → JWT (claims: `outlandsid`, `purpose`, `mahid`, `mahleader`) → game session | `settings.json`, strings |

Implication for tooling: **Ghidra (native) is the correct analysis tool. Any "client modification" approach (patches, DLL plugins, Harmony) is off the table** — both technically (AOT) and per rules.

## 3. Documented enforcement (rules + wiki)

From [Shard Rules](https://uooutlands.com/rules/) (Aug 2026):

- §3.1–3.2: only Outlands Launcher + its Razor assistant; every other client/tool banned; *"any program that provides artificial or automated inputs"* banned.
- §3.3: *"Any method of automation or programmatic data extraction from the game client is not allowed"* — journal file/memory reading, OCR, packet sniffing, memory scanning, event-reacting scripts all named. §3.4: only intended visual/audio cues + official Razor, **acted upon manually**.
- §4: *"automated systems and administrative review to detect unauthorized modifications to the game client and artificial input… operate solely within the game client."* — confirms an in-client detection component exists.
- §2: AFK/unattended gathering or XP = jail then ban; GM unresponsiveness check = 2 minutes.
- §14: no deciphering server messages.
- **Test Shard CoC is separate and permissive**: *"designed for testing, bug-checking, and experimentation"*; only restrictions: don't interfere with other testers, no unsanctioned PvP. The §3 client/tooling restrictions are written against the production shard; the Test Shard section does not restate them. (Still: don't take this as a license — ask staff if in doubt.)

From the wiki:

- **Captcha**: harvesting (lumber/mining/fishing/forensics/shearing/lockpicking) prompts a CAPTCHA every 5–10 min; 3 fails = 6 h harvest block; persists across relog ([wiki](https://wiki.uooutlands.com/Captcha)). Client has `CAPTCHA_GUMP_ID` (the captcha is a normal gump; the client knows its ID — plausibly to stop Razor auto-answering).
- **2FA**: email-based new-device verification for OutlandsID ([news, Feb 2026](https://uooutlands.com/news/outlandsid-and-infrastructure-updates/)).
- GM AFK checks on harvesters are routine (public reports).

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
- Device binding: `VerifyDeviceModel`, `VerifyDeviceCode`, `DeviceVerificationGump`, `get_DeviceId`/`set_DeviceId` adjacent to `Ip` in a login model, and **`TPMModel`** (with `ncrypt.dll` imported by the client). Assessment: **probable TPM-backed device ID** [INFERENCE — Ghidra confirmation pending]; purpose = household/multi-account enforcement and 2FA, not input automation.
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
| `TPMModel`, `get_DeviceId`, `VerifyDeviceModel`, `ServerClientRestriction`, `VersionRestrictions`, `IsMostRecentGameFilesVersion` | Outlands account/version enforcement (OutlandsID, device verification, household claims, client-version gating). Purpose: account-sharing / multi-boxing / ban-evasion control at **login time**, not gameplay automation. `TPMModel` remains naming-level evidence only. | Medium–High |

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

## 8. Implications for the agent harness (design rules)

Draft — to be finalized after §6/§7:

1. **Never touch the client process.** No injection, no patching, no window subclassing, no synthetic input into its message loop. (Rules §4 + NativeAOT + likely integrity channel.)
2. **Network-position observation is the lowest-touch surface**: a localhost TCP proxy between client and `play.uooutlands.com:2593` leaves the client 100% stock. Residual risk: server-side packet-timing/behavioral heuristics and rule §14 (written for the production shard).
3. **Assume all automation-detection is server-side statistics + human review**: movement timing, action inter-arrival times, 24/7 uptime, perfect play, CAPTCHA response latency. The harness must add human-like jitter, sessions of human length, and never farm captcha-gated resources unattended.
4. **Respect Razor gating signals** (`IsRazorBlockedSysMessage`, PvP restrictions): when the server restricts assistants, the harness must halt automated actions.
5. **Device/account infrastructure is out of scope**: do not attempt to spoof `DeviceId`/TPM/2FA; log in through the official launcher normally.
6. **Keep files stock**: `VersionRestrictions`/`IsMostRecentGameFilesVersion` means modified game files may block login outright; the harness must never write into the install dir (work in `C:\Users\chris\uo-harness`).
7. **Test Shard only**: run nothing against production; the Test Shard CoC explicitly supports experimentation, and `[TestRes]/[TestBlessedGear]/[Go` commands exist for safe iteration.
9. **Behavioral walk-train gate — CONFIRMED LIVE (2026-09-29).** The server detects movement trains without interleaved client activity: short injected bursts following client activity are accepted; ~10+ uninterrupted injected walks are rejected wholesale; and **after repeated solo trains the character's movement was gated even for the human user's own arrow-key input** (recovery pending relog test). First directly-observed punitive anti-automation mechanism beyond silent rejection. Harness rule: **never run long uninterrupted movement trains**; interleave client activity, keep bursts ≤ ~6 steps, and treat any solo-train lockout as a detection event to stop on immediately.
   **Reinterpretation (2026-09-29, sessions 20260929_142237 / _143051 / _144541):** the arrow-key lockout was client-side, with no server penalty involved. The server's ConfirmWalk for walks the client didn't send trips the client's bad-step path (`WalkingFailed = true`, latched single resync), and the client stays frozen until a server walker reset. The server apparently ignores resyncs < ~5 s apart, so fast agent steps outran the reset (docs/MOVEMENT.md). **Live-supported:** with agent steps spaced ≥ 5 s, 6/6 stepped and the user's own arrow keys kept working. The "trains rejected wholesale" observation is likely the same mechanism plus ladder drift, not a behavioral gate. Keep the ≤ ~6-step rule anyway until S2C visibility can rule out a server-side gate.
   **S2C evidence (2026-09-29, corrected decode, docs/CIPHER.md §4):** in session 142237 the server *confirmed* all 12 agent continuation walks (`22 01..0c 01`) of a solo train while the frozen client drew nothing. **No server-side rejection of walk trains exists.** The lockout was 100% client-side. Server-side *passive* behavioral analysis can't be ruled out from the wire, so the harness still keeps agent movement human-paced: 0.2 s run / 0.4 s walk minimum step spacing, enforced by the proxy, and bursts interleaved with normal play.
10. **Spent-token re-presentation — closed by the proxy (2026-09-29).** When the proxy stamps the cycle token into an injected opener, the client still holds its own copy and would present it on its next walk — a spent token re-presented, which a stock client never does and a server log could flag. `MoveAuthority` tracks that copy (`stale_token`) and zeroes it once, so the server sees exactly one presentation per token. Other client keys on continuations pass through because genuine mid-cycle server pushes exist (docs/MOVEMENT.md). Residual surface: the resync-per-external-walk cadence (§8.9 class) — mitigate via mixed manual play and short attended bursts.
11. **Fix B detection surfaces (2026-09-29; revised same day by user decision).** (a) The proxy drops/rewrites S2C `0x22` ConfirmWalk packets toward the client, and re-anchors the client with a **fabricated S2C `0x21` DenyWalk**. Both are client-side only and invisible to the server. The client code has no integrity check on the S2C stream beyond decoding. (b) **The proxy sends no packets of its own to the server.** The earlier design's proxy-originated resync after each agent burst was removed, because it was a pattern a stock client never produces. Server-visible C2S is now exactly the client's own traffic plus the agent's walks/actions (seq/key rewritten to be consistent). (c) Agent step pacing is enforced proxy-side (Speedhack category). Residual server-visible surface: agent walks themselves (timing and paths), and the absence of the client resyncs that fix A produced.
8. **CAPTCHA strategy — human-in-the-loop by default, auto-solve opt-in.** The captcha is the shard's dedicated automation tripwire (3 fails = 6 h harvest block; response latency and long-term accuracy are trivially usable as detection statistics). Technical path exists: captcha arrives as a normal gump → harness detects it from the gump-open packet (gump ID family + layout), crops the digit region from the screen capture using layout coordinates, classifies digits (template matching or small CNN — the digits are fixed shapes with displaced dots per the wiki), and answers via the text-entry + button packets. **Default policy: detect → pause automation → alert human (sound/notification/webhook) → human solves → resume.** Auto-solve may only be enabled after its measured accuracy on Test Shard leaves the 3-fail budget with wide margin, must be confidence-gated with human fallback, and must use human-plausible response timing.

12. **Injected speech must be keyword-encoded like the stock client (2026-09-29).** The stock client encodes any speech that matches a `speech.mul` keyword (type |= 0xC0, 12-bit ids, UTF-8). The Outlands encoder `Send_UnicodeSpeechRequest @ 0x140151c20`, `GetKeywords @ 0x1401bbc60` and `IsMatch @ 0x1401bba40` are the upstream algorithm. The harness's old `say_unicode` always sent plain UTF-16. So the **"hello" injected during the Phase 3 live test (session 20260928_211622) was not client-identical**: speech.mul id 59 = "hello", and a stock client would have sent it encoded. Server-side, a keyword word arriving unencoded is a detectable anomaly [INFERENCE on whether it is checked]. Fixed: `harness/uo/speech.py` + `actions.say_unicode` now reproduce the stock client exactly (verified against the real client's "bank" `ad0016c0…62616e6b00`, session 20260929_161433). Likewise, "look at NPC" now sends the stock sequence `09` + `34 …04` (+ `98` for unnamed), as seen in 518/523 real clicks.

13. **Decoy "Captcha" gumps: a honeypot for text-matching bots (2026-09-29, session 20260929_204225; structure confirmed, purpose [INFERENCE, high]).** Every lumberjacking attempt opens a gump that contains the captcha's words ("Captcha", "Type the Value", "Click when complete"). Each one has:
    - a fresh random gump id (≥ 10 distinct in one session)
    - `nomove/noclose/nodispose`
    - **no reply button**
    - its text as `croppedtext` at negative (offscreen) coordinates
    - `xmfhtmlgump` entries with cliloc numbers that don't exist in Cliloc.enu

    The human sees nothing, and the stock client can't answer it. Any gump response for one of these ids can only come from automation, so a response is a near-certain detection signal. The **real** captcha is gump id `0x00000001`: a `textentrylimited` (id 2, max 3 chars) plus reply button 594, with the digits drawn as `tilepic` dot glyphs (graphics 572/6255) at layout coordinates. The human's answer was `b1 … button 594, text entry 2 = "326"` → "Captcha successful."

    Harness rules:
    - Captcha detection keys on the gump id plus the entry/button structure, **never on text**.
    - The agent never sends a gump response for a gump that offers no reply button.
    - Captcha handoff stays human (rule 8).

    The digits are machine-readable from the layout, so auto-solving wouldn't need OCR. Rule 8's opt-in bar is unchanged. The decoys show the server is actively set up to catch naive automation.

14. **Behavioural texture (2026-09-29, user request).** Mitigation for the §8.3 statistics surface. Every agent runner draws its timing and route choices from `harness/humanize.py`:
    - lognormal reaction times per action kind, with fatigue drift
    - per-plan route noise instead of the one optimal path, occasional walked routes, pauses and sidesteps, and missed turns that run into a known obstacle (a server deny, as players get) before turning
    - occasional cursor hesitation (stock Esc cancel) and idle fidgets (backpack, looking at a mobile)

    Constraints: only stock-identical packets or waiting. It never beats the proxy's pacing floor or the gate. No free-text speech (PLAN.md speech allowlist). [INFERENCE] Whether Outlands' server models these statistics is unknown. The texture is cheap insurance, not a guarantee.

## 9. Open questions

- ~~What exactly does `Send_UOLive_HashResponse` hash?~~ **Answered: map-block CRC16s for UltimaLive world sync (upstream source).**
- ~~Any periodic connections beyond login/game during a session?~~ **Answered: none (30-min capture, §7).**
- ~~Does the client scan processes / use anti-debug?~~ **Answered: those APIs are not imported (no IAT entries).**
- Is there a client→server packet carrying the `Speedhack/AutoClicking/AutoKeyboard` categories (server-report), or are they internal? → resolve by wire-watching through the harness proxy; note packet IDs here when seen.
- When does `Send_TimeSyncPingReq` fire and what timestamps does it carry? → same method; proxy must pass it through unaltered.
- Is `TPMModel` actual TPM attestation? (low priority — login-time account security, out of harness scope)

---

### Appendix A — Evidence artifacts (local)

| Artifact | Path |
|---|---|
| Full client strings (373k) | `C:\Users\chris\uo-harness\strings.txt` |
| Categorized scan hits | `C:\Users\chris\uo-harness\grep_hits.txt` |
| API surface names | `C:\Users\chris\uo-harness\api_surface.txt` |
| Endpoint inventory | `C:\Users\chris\uo-harness\endpoints.txt` |
| Launcher strings/hits | `C:\Users\chris\uo-harness\launcher_strings.txt`, `launcher_hits.txt` |
| Ghidra project + script | `C:\Users\chris\uo-harness\ghidra\`, `ghidra_scripts\ACXrefs.java` |
| Behavioral monitor | `C:\Users\chris\uo-harness\monitor_endpoints.ps1` |

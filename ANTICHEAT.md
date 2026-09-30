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
    - Captcha detection keys on the gump id plus the entry/button structure, **never on text and never on a fixed button id**.

    **Update (live 2026-09-29, session 20260929_220932): the submit button id is random per captcha.** The demo's captcha used 594; the next one used **843**. Its dot glyphs were also different graphics (11695 and 2457). Guide button 1 and text entry 2 were the same both times. The first detector required button 594, so it missed the second captcha. There was no handoff and no beep, and the user solved it unprompted. Detection now requires gump id 1, text entry 2 and a reply button other than Guide 1, and the offline e2e uses a submit id other than 594. [INFERENCE] The randomisation targets bots that replay a fixed button id.
    - The agent never sends a gump response for a gump that offers no reply button.
    - Captcha handoff stays human (rule 8).

    The digits are machine-readable from the layout, so auto-solving wouldn't need OCR. Rule 8's opt-in bar is unchanged. The decoys show the server is actively set up to catch naive automation.

14. **Behavioural texture (2026-09-29, user request).** Mitigation for the §8.3 statistics surface. Every agent runner draws its timing and route choices from `harness/humanize.py`:
    - lognormal reaction times per action kind, with fatigue drift
    - per-plan route noise at the scale of 6×6-tile map cells instead of the one optimal path, with zig-zag stretches regrouped into straight runs (`nav.straighten`), occasional walked routes, pauses and sidesteps
    - steps at the stock client's held-key cadence (200 ms run / 400 ms walk plus frame jitter, measured from the previous send); the pauses sit between stretches, not inside them
    - doors opened like the client's auto-open (`PlayerMobile.TryOpenDoors`): the open-door request goes out right after the turn or step that faces a door on the next tile, before stepping into it
    - occasional cursor hesitation (stock Esc cancel) and idle fidgets (backpack, looking at a mobile)

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
    - PvP stays forbidden: the Test Shard Code of Conduct, plus Heat of Battle (recall and inn
      room blocked).
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

## 9. Open questions

- ~~What exactly does `Send_UOLive_HashResponse` hash?~~ **Answered: map-block CRC16s for UltimaLive world sync (upstream source).**
- ~~Any periodic connections beyond login/game during a session?~~ **Answered: none (30-min capture, §7).**
- ~~Does the client scan processes / use anti-debug?~~ **Answered: those APIs are not imported (no IAT entries).**
- Is there a client→server packet carrying the `Speedhack/AutoClicking/AutoKeyboard` categories (server-report), or are they internal? → resolve by wire-watching through the harness proxy; note packet IDs here when seen.
- When does `Send_TimeSyncPingReq` fire and what timestamps does it carry? → same method; proxy must pass it through unaltered.
- Is `TPMModel` actual TPM attestation? (low priority — login-time account security, out of harness scope)

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
| A10 | Session shape: 20260930_123206 was 4.6 h connected with 6 377 agent C2S and essentially no human input except 2 captchas. Captcha cadence is the only attendance check. | log counts | Human-length sessions; operator-liveness requirement |

**Status (2026-09-30, same day):** A1–A8 fixed in code, offline-tested; not yet run live.

| # | Fix | Test |
|---|---|---|
| A1 | client-only cancel + drop of the client's reply for the spent cursor (§8.18; `SessionTap.release_client_cursor`) | `test_movement.py` e2e |
| A2 | `CONFIRM_TIMEOUT_S` 3.0 s; expired walks stay recognizable for 5 s (`MoveAuthority.late`): a late confirm is hidden and moves the ladder past its seq; agent walks wait out that window instead of re-sending the seq; agent walks also stop at 5 unconfirmed (stock `MAX_STEP_COUNT`) | `test_movement.py` unit |
| A3 | `menu` and `buy` send `09` then `bf 0013` back to back (`ctl.context_menu_packets`) | `harness/test_ctl.py` |
| A4 | `dclick`/`single_click`/`menu`/`menu_pick`/`attack` need the entity (or the mobile/ground item holding it) in the world model within 18 tiles (`ctl.not_clickable`); `menu_pick` needs that serial's context menu open with that index | `harness/test_ctl.py` |
| A5 | bump texture removed; doors opened ahead (§8.14); `ctl act walk` never sends a step the map rules refuse | `harness/test_mover.py` |
| A6 | every 0xB1 is built by `actions.gump_reply` from the gump's layout (ctl and the lumber room menu) | `harness/test_actions.py` |
| A7 | `single_click` on a mobile adds its `0x34`; `attack` sends `0x34` while the mob's hits are unknown; `dclick` on a mobile in war mode is refused; `warmode` sends nothing when already in that state | `harness/test_ctl.py` |
| A8 | stock held-key cadence, cell-scale route noise, `nav.straighten` (§8.14) | `harness/test_nav.py` + offline route measurement |

A9 and A10 remain open.

**Live run 2026-09-30 18:28-18:56 (session 20260930_182751, overseer 10-log lumber trip at Horseshoe
Bay):**
- 657 agent walks: step gaps p10/median/p90 207/212/217 ms (min 203), 20 % heading changes
- 0 client resyncs, 0 `walk_rejected`
- 2 agent denies. One at (2003,2227) S, on a tile the map rules call walkable (cause unknown).
  One diagonal NW at (2027,2218) through the corner of a closed double door (below).
- 6 open-door requests, 5 of them with no deny before them. The sixth was the auto-open after the
  turn that followed that diagonal deny.
- 3 agent target answers, 3 client-only cancels, 3 client replies dropped; 0 client `0x6C`
  reached the server
- 4 gump closes, 24 re-anchors

**New finding from that run:** the planner treats doors as passable, and the auto-open only covers
the facing tile. So a diagonal step past a closed door on a corner tile is sent and denied. The
stock client's CanWalk counts a closed door as a wall and wouldn't send that step. [open]

**Other gaps:** nothing verifies the connected shard is the Test Shard. divert_nat.py hardcodes
74.91.115.123, the JWT carries no shard claim, and whether production resolves to the same IP is
unknown. The installed client was patched to 1.0.2.550 on 2026-09-28 (JWT `version`), while the RE
behind `actions.py` is from 1.0.2.544. Captured 550 traffic frames cleanly, but nothing guards against
a future layout change. README.md and INTERCEPTION.md no longer claim a byte-identical relay
(corrected 2026-09-30).

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

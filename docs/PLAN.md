# Build plan — uo-harness

Decision record + phases. Research basis: [`../ANTICHEAT.md`](../ANTICHEAT.md).

## Target environment

**Test Shard only** (user decision, 2026-09-27). Rationale: full autonomy on the production shard violates rules §3.3/§4 (automation, programmatic data extraction, artificial input) and risks the OutlandsID; the Test Shard CoC explicitly exists for "testing, bug-checking, and experimentation". Production use is permanently out of scope for this harness.

## Why a relay-only proxy (alternatives considered)

| Approach | Verdict |
|---|---|
| **Localhost TCP proxy (chosen)** | Client 100% stock; full structured game state from packets; actions are protocol-identical to play; no anti-cheat tamper surface on the machine. |
| Custom ClassicUO plugin | Client is NativeAOT — no plugin loading possible; also rules §3.2. Dead. |
| Binary patching / injection (Ghidra-guided) | Technically hard (AOT, stripped), violates rules §4 outright, detectable via version checks. Dead. |
| CV/screen + synthetic input | OCR + artificial input are both explicitly named in rules §3.3/§3.2; lowest-fidelity state. Dead as primary; screen capture survives only as a sanity/verification channel (not OCR-as-state) and for CAPTCHA cropping. |
| Razor scripts only (in-client) | Sanctioned surface but capped: no programmatic state extraction, PvP-gated, captcha-gated; can't host an agent. Survives as the optional live "copilot" mode (agent generates Razor scripts, human runs them manually). |
| Local ServUO sandbox | Deferred, not rejected: useful if Test Shard access becomes a problem; protocol-compatible family, zero-risk iteration. Keep as fallback. |

## Phases
### Phase 1 — Proxy core
Python asyncio TCP relay: client→localhost:2593→upstream. Config switch via `-ip`/`-port` CLI args (no settings.json edit needed; upstream CUO `Main.cs` supports them). Huffman decompression (server→client) + packet framing. Passthrough login.
**Done when**: client logs into Test Shard and plays normally through the proxy; full packet log captured.
**Watch-items**: record every occurrence of `Send_TimeSyncPingReq` and any packet carrying the `Speedhack/AutoClicking/AutoKeyboard` categories → feed packet IDs back into ANTICHEAT.md §9.

**Status 2026-09-28: ✅ DONE — live.** Client logs into the Test Shard and plays normally through the proxy (TestWorth in-game 2026-09-28 14:12; full session decoded: JWT login, char select, world dump, 50+ keepalives, speech decrypted with session key 0x07). Chain: WinDivert NAT → proxy → server (`docs/INTERCEPTION.md`). Offline loopback test ALL PASS; live run has 2 S2C desyncs from unmapped custom packets → Phase 2 material. C2S obfuscation solved (`docs/CIPHER.md`). Elevation + operation runbook documented.

### Phase 2 — World model
Parse the core packet set into a queryable state store: player stats/skills, mobile/item draw+update, container contents, gump open/close/layout, journal/sysmessages, targeting, movement acks. Ground truth: upstream `ClassicUO-main` packet handlers.
**Done when**: live state dump matches in-game reality (verified against screen captures); state survives a `[Go` warp and a dungeon transition.
**Status 2026-09-28: ✅ DONE.** `harness/world/` (declarative parsers per WORLDMODEL.md v2 layouts, StateStore, event queue) + `harness/replay.py`; 118 checks green (per-parser unit + replay on both captured sessions). The 0x00 "world-state" stream proven to have **no client parser** (companion-app channel — nothing lost; see `docs/WORLDSTATE.md`). Screen-capture validation replaced by replay-state assertions; `[Go` warp validation deferred to live agent sessions.
**Status 2026-09-29: REOPENED.** The S2C decode used by `harness/replay.py` and all Phase 2 analysis was wrong (missing XOR with prelude byte 11, docs/CIPHER.md §4). The "world-state stream" and the "custom S2C dialect" were decode garbage. The parsers and tests are being re-validated against correctly decoded captures, where the content is standard UO.

### Phase 3 — Action API
Client→server action packets: walk/pathfind, dclick, use skill, cast+target, lift/drop, gump button/text responses, say, `[`-commands (Test Shard has `[TestRes`, `[TestBlessedGear`, `[Go` for fast iteration).
**Done when**: scripted closed-loop task (e.g. bank run or recall to location + back) completes unattended on Test Shard with human-like pacing.
**Status 2026-09-28: code-complete, live validation pending.** `harness/actions.py` (8 proven builders + WalkSequencer) and the proxy `InjectionHub` (control listener :25941; session-key XOR; tap-integrated; malformed-frame isolation). All suites green (actions/world/proxy). Live checklist staged: inject speech → walks → dclick → cast → lift/drop while watching the keepalive cadence stay untouched. Closed-loop unattended task = Phase 4 acceptance.
**Update 2026-09-29: walks.** Movement acceptance is owned by the proxy's `MoveAuthority`: it rewrites every C2S walk (client + injected) onto one seq ladder and stamps the cycle token (8 login / 1 post-resync) into the first walk of each cycle. Decision rationale: proxy-side ownership (vs. making each tool track tokens) gives every tool correct movement with zero bookkeeping and keeps client and agent walks from desyncing each other; the client stays the source of resyncs (suppression proved wrong, docs/MOVEMENT.md). Known gap: silent server rejections can drift the ladder until the next resync.
**Update 2026-09-29: agent walk gate.** Live test showed that the server's ConfirmWalk for agent steps locks the client's walker (bad-step path). User decision: **A now, B later**. (A) The proxy lets through one agent walk per movement cycle, only as the cycle opener and after the resync has settled. This is safe and slow (~1 step per 0.7 s) and keeps the resync-per-step signature. (B) The proxy hides agent ConfirmWalks from the client and re-anchors it. That needs S2C framing fixed plus a Huffman re-encoder, because flush segments ≠ packets. See docs/MOVEMENT.md.
**Update 2026-09-29: fix A validated live** (session_20260929_144541): 6/6 agent steps with ≥5 s client-resync spacing, and the client's own walking unaffected afterwards. Phase 3 movement now works attended/unattended at ~1 agent step per 5 s. Fix B remains the path to normal-speed agent movement.
**Update 2026-09-29: fix B implemented (offline-proven), pending live test.** The S2C decode was corrected (XOR with prelude byte 11, docs/CIPHER.md §4), which made the server's movement packets visible. Decision: the proxy's MoveAuthority follows server seeds/confirms instead of inferring from C2S, hides agent confirms from the client, rewrites client confirms to client seqs, paces agent steps (0.2/0.4 s), and re-anchors the client with one resync per agent burst (≥ 5.6 s spacing). Alternative rejected: re-anchoring by passing the last agent confirm through, so the client's own bad-step resync does it. That locks the client whenever the server ignores the resync. Proof: `test_movement.py` (unit + e2e).

### Phase 4 — Agent runtime
LLM planner over the world model + skill library; safety rails (rate limits, Razor-gating halt, captcha human-handoff per ANTICHEAT.md §8 rule 8, kill switch, session length caps).
**Done when**: agent completes a multi-step objective from natural language (e.g. "restock reagents from the bank and return") with captcha/gating handoffs demonstrated.

### Phase 5 (optional) — Production copilot
Rules-compliant live mode: agent generates Razor scripts into `Data/Plugins/Assistant/Scripts/`; human reviews and runs them manually. No autonomy, no data extraction. Only phase allowed to touch the production shard, and only as a file generator.

## Risks

- **Protocol drift**: Outlands patches frequently (client is days old at research time). Parser must be tolerant of unknown packets (log-and-forward) with a packet-ID registry that's easy to update.
- **Custom login/auth**: the OutlandsID HTTPS handshake is not yet mapped at byte level; proxy only needs the post-auth game connection, but settings/login responses may bind the session to the original destination — validate in Phase 1.
- **Behavioral statistics**: even perfect protocol emulation can be detected by play patterns; pacing, session length, and task selection are the mitigation (§8 rules).
- **Staff attention on Test Shard**: keep activity non-disruptive (Test Shard CoC: don't interfere with others' testing).

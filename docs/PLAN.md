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
Python asyncio TCP relay: client→localhost:2593→upstream. Config switch via `settings.json` (`ip`/`port`). Huffman decompression (server→client) + packet framing. Passthrough login.
**Done when**: client logs into Test Shard and plays normally through the proxy; full packet log captured.
**Watch-items**: record every occurrence of `Send_TimeSyncPingReq` and any packet carrying the `Speedhack/AutoClicking/AutoKeyboard` categories → feed packet IDs back into ANTICHEAT.md §9.

### Phase 2 — World model
Parse the core packet set into a queryable state store: player stats/skills, mobile/item draw+update, container contents, gump open/close/layout, journal/sysmessages, targeting, movement acks. Ground truth: upstream `ClassicUO-main` packet handlers.
**Done when**: live state dump matches in-game reality (verified against screen captures); state survives a `[Go` warp and a dungeon transition.

### Phase 3 — Action API
Client→server action packets: walk/pathfind, dclick, use skill, cast+target, lift/drop, gump button/text responses, say, `[`-commands (Test Shard has `[TestRes`, `[TestBlessedGear`, `[Go` for fast iteration).
**Done when**: scripted closed-loop task (e.g. bank run or recall to location + back) completes unattended on Test Shard with human-like pacing.

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

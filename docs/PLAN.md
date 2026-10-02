# Build plan — uo-harness

Decision record + phases. Research basis: [`../ANTICHEAT.md`](../ANTICHEAT.md).

## Why a localhost proxy (alternatives considered)

| Approach | Verdict |
|---|---|
| **Localhost TCP proxy (chosen)** | Client 100% stock; full structured game state from packets; actions are protocol-identical to play; no anti-cheat tamper surface on the machine. |
| Custom ClassicUO plugin | Client is NativeAOT — no plugin loading possible. Dead. |
| Binary patching / injection (Ghidra-guided) | Technically hard (AOT, stripped), detectable via version checks. Dead. |
| CV/screen + synthetic input | OCR and artificial input are both low-fidelity and detectable. Dead as primary; screen capture survives only as a sanity/verification channel (not OCR-as-state) and for CAPTCHA cropping. |
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
**Status 2026-09-29: REOPENED → re-validated offline on the corrected decode.** The S2C decode used by `harness/replay.py` and all Phase 2 analysis was wrong (missing XOR with prelude byte 11, docs/CIPHER.md §4). The "world-state stream" and the "custom S2C dialect" were decode garbage. With `uo.s2c`, all 18 captures replay with 53 277 packets, 0 length mismatches, 0 parse failures, 0 anomalies. Nine parser layouts were fixed against real packets and decompiled handlers (0x20 carries any mobile, 0x77, 0x78 V12, 0x1B, 0x11, 0x3A, 0xDD, FF sub 8, plus new 0x1C/0xAE/0x98/0xA9; docs/WORLDMODEL.md). The replay of session 144541 yields self TestWorth at (0x7A6, 0xA25) and named NPCs with positions. The remaining done-criterion is a live dump vs in-game check.

### Phase 3 — Action API
Client→server action packets: walk/pathfind, dclick, use skill, cast+target, lift/drop, gump button/text responses, say, `[`-commands (Test Shard has `[TestRes`, `[TestBlessedGear`, `[Go` for fast iteration).
**Done when**: scripted closed-loop task (e.g. bank run or recall to location + back) completes unattended on Test Shard with human-like pacing.
**Status 2026-09-28: code-complete, live validation pending.** `harness/actions.py` (8 proven builders + WalkSequencer) and the proxy `InjectionHub` (control listener :25941; session-key XOR; tap-integrated; malformed-frame isolation). All suites green (actions/world/proxy). Live checklist staged: inject speech → walks → dclick → cast → lift/drop while watching the keepalive cadence stay untouched. Closed-loop unattended task = Phase 4 acceptance.
**Update 2026-09-29: walks.** Movement acceptance is owned by the proxy's `MoveAuthority`: it rewrites every C2S walk (client + injected) onto one seq ladder and stamps the cycle token (8 login / 1 post-resync) into the first walk of each cycle. Decision rationale: proxy-side ownership (vs. making each tool track tokens) gives every tool correct movement with zero bookkeeping and keeps client and agent walks from desyncing each other; the client stays the source of resyncs (suppression proved wrong, docs/MOVEMENT.md). Known gap: silent server rejections can drift the ladder until the next resync.
**Update 2026-09-29: agent walk gate.** Live test showed that the server's ConfirmWalk for agent steps locks the client's walker (bad-step path). User decision: **A now, B later**. (A) The proxy lets through one agent walk per movement cycle, only as the cycle opener and after the resync has settled. This is safe and slow (~1 step per 0.7 s) and keeps the resync-per-step signature. (B) The proxy hides agent ConfirmWalks from the client and re-anchors it. That needs S2C framing fixed plus a Huffman re-encoder, because flush segments ≠ packets. See docs/MOVEMENT.md.
**Update 2026-09-29: fix A validated live** (session_20260929_144541): 6/6 agent steps with ≥5 s client-resync spacing, and the client's own walking unaffected afterwards. Phase 3 movement now works attended/unattended at ~1 agent step per 5 s. Fix B remains the path to normal-speed agent movement.
**Update 2026-09-29: fix B implemented (offline-proven), pending live test.** The S2C decode was corrected (XOR with prelude byte 11, docs/CIPHER.md §4), which made the server's movement packets visible. Decision: the proxy's MoveAuthority follows server seeds/confirms instead of inferring from C2S, hides agent confirms from the client, rewrites client confirms to client seqs, paces agent steps (0.2/0.4 s), and re-anchors the client with one resync per agent burst (≥ 5.6 s spacing). Alternative rejected: re-anchoring by passing the last agent confirm through, so the client's own bad-step resync does it. That locks the client whenever the server ignores the resync. Proof: `test_movement.py` (unit + e2e).
**Update 2026-09-29: client-only re-anchor (user decision).** The proxy-originated C2S resync was removed: it was a server-visible pattern a stock client doesn't produce. The client is now re-anchored with a fabricated S2C `0x21` DenyWalk that only the client sees. Outlands' `DenyWalk` handler (`@0x140189200` → `WalkerManager.DenyWalk @0x14030ae40`) resets the walker, places the player at (x, y, z) and sets facing. The proxy tracks the server-true position from the server's self `0x1B`/`0x20`/`0x77`/`0x21` plus confirmed walks, and a walk in a new direction only turns (verified on 144541: the tracker equals the server's final self `0x77`). Alternatives rejected: a fabricated self `0x20`, because the V10 handler ignores position for the player (`protocol_handlers.c:18520`); a fabricated full resync reply, because it would need a seed token the server never issued. Rejected walks now rewind the ladder (the server doesn't advance on rejection, session 142237), and 3 in a row stall agent walks. Known limit: z comes from the last server report.
**Update 2026-09-29: fix B VALIDATED LIVE** (session_20260929_161433): agent and client walking mixed freely (57 agent + 30 client walks), the client re-anchored correctly 5 times via fabricated `0x21`, 0 resyncs, 0 proxy-originated server packets. Agent movement now runs at normal speed (paced 0.2/0.4 s). Remaining Phase 3 done-criterion: an unattended closed-loop task (bank run).
**Status 2026-09-29: ✅ DONE — unattended bank run completed live** (session_20260929_163420, `harness/errand_bank.py --start 1963,2597`). Sequence:
- walked 10 steps from (1961, 2605) to the start (1963, 2597)
- found "Len the banker" from a label the client itself requested (the stock client auto-sends `09`+`34` to mobiles coming into range)
- walked 13 known steps to (1955, 2589), 8 tiles from Len
- said "bank" (byte-identical to the stock client's keyword-encoded packet)
- the server opened the bank box: `0x2E` layer-0x1D item `0x44D78CA8` + `0x24` gump `0x4A`
- walked 13 steps back to exactly (1963, 2597)

26 s total, 36 moves, 0 blocked, 2 client-only re-anchors, 0 resyncs, 0 proxy-originated server packets. Architecture:
- the proxy's state port (live world model + movement truth)
- `harness/nav.py` walk memory (known-walkable ground from captures and live `step` rows; optimistic A* that learns blocked moves from server denies)
- `harness/uo/speech.py` stock-identical keyword speech

Offline proof: `test_errand.py` (proxy + runner + a simulated world with a wall and two NPCs).

### Phase 4 — Agent runtime
LLM planner over the world model + skill library; safety rails: captcha auto-solve (ANTICHEAT.md §8.8), plus pause and kill switch in the visualizer.
**Done when**: agent completes a multi-step objective from natural language (e.g. "restock reagents from the bank and return") with captcha auto-solve demonstrated.
**Design decisions 2026-09-29 (user):**
- **Planner = standalone Python loop on the Anthropic API (tool use)**, not an MCP server driven by an interactive session. The LLM chooses *skills* (deterministic closed-loop Python controllers generalised from `errand_bank.py`: goto, open_bank, move_items, buy, cast, use_skill…), never raw packets. Rejected: LLM picking raw actions (latency/cost, pacing becomes the model's job); LLM-authored skill code (Voyager-style), deferred.
- **Map data: read directly from the real install dir** (map/statics/tiledata; read-only, never write). This replaces walk-memory-only navigation for unexplored ground, so exploring doesn't mean repeated server denials.
  - **Prerequisite found 2026-09-29:** Outlands stores map, statics, tiledata and art in proprietary `.uoo` containers (docs/NOTES.md). Walkability from map files therefore needs the `.uoo` format reverse-engineered from the client's loader first. `facet00.mul`, a standard-format 1 px/tile colour picture, is decodable today, but it is only visual: it carries no heights and no walkability.
- **Handoff alert: sound** (first version).
- **Full autonomy**, with **pause and kill-switch buttons in the visualizer** (docs/VISUALIZER.md). Pause lets the user take control and resume later; kill stops the agent. The proxy enforces both with a flag that rejects all agent injection, so no skill can bypass them.
- **Captcha: human-solved by default, auto-solve behind a viz toggle (user decision 2026-10-01).** The solver (`harness/captcha.py`) stays built and tested, but the runner only uses it when the memory store's `meta.captcha_mode` is `auto`; unset means `human` (pause + beep until the solve is seen in the client). The `captcha [human|auto]` toggle in the viz header writes it (`POST /api/captcha`, docs/VISUALIZER.md §2.2a), and the runner reads it at every captcha and while waiting. The setting lives in the store because the runner and the viz are separate processes and both already open it. A runner CLI flag would need a restart to change, and the proxy gate is about injection, not this choice. The Phase 4 done criterion's "captcha auto-solve demonstrated" now means a live run with the toggle on `auto`.
- **No further proxy rails (user decision 2026-09-29):** no packet-id allowlist, per-type rate limits, or automatic yield when the human acts; the pause button covers taking control. The existing proxy walk pacing (0.2/0.4 s) stays, because it is part of the movement design. Session length: see the next item.
- **Mandatory breaks and a daily cap, enforced in code (user decision 2026-09-29).**
  - A forced pause every ~2 h of agent activity, with the interval jittered ±20 min.
  - Break length 15–30 min, jittered (default chosen by the agent; adjustable).
  - At most 8 h of agent-active time per local calendar day. Paused and break time don't count.
  - The usage counter is persisted to disk, so restarts don't reset it.
  - Enforced through the same proxy flag as pause/kill, so the planner can't bypass it. The visualizer shows the time left until the next break and the daily budget.
  - **Implemented 2026-09-29:** `harness/agent_gate.py` (`AgentGate` plus the CLI), checked first in `InjectionHub.inject`.
    - Controlled on the state port with `{"op":"gate","action":...}`; every state response carries `gate`.
    - State is persisted to `<logdir>/agent_budget.json`: the live file is `logs/agent_budget.json`, and test proxies with their own logdirs never touch it.
    - Agent-active time = the gaps of at most 60 s between accepted injections. An idle gap of at least 15 min counts as a break taken.
    - A kill is latched and survives restarts; `rearm` is CLI-only.
    - Proof: `harness/test_agent_gate.py`.
  - **Break warning (user request 2026-10-01).** A break that started wherever the agent stood left the character motionless in the field (a GM's cue; ANTICHEAT.md §10 A9). Now the used-up interval makes the break *due*: the gate stays open for up to 10 min (`BREAK_GRACE_S`), `ctl wait` posts one `break_due` juncture (attention), and the overseer decides: stop the task, get home, `ctl break`. With nobody acting, the break starts by itself when the grace runs out, as before. Rejected: the runner timing its own return from `next_break_in_s` (only the overseer knows what else is going on, and a stopped runner can't), and an unbounded grace (it would turn mandatory breaks into optional ones).
- **In-game speech is allowlisted keywords/commands only** (user decision 2026-09-29). Allowed: NPC trigger words such as `bank` and `vendor buy`, `[`-commands, and a few innocuous phrases. The LLM never writes free text into the game (AGENTS.md constraint 4). The wire encoding is unaffected: `say_unicode` keyword-encodes like the stock client. Free text through a filter was rejected for now, because no filter can rule out every revealing line.
- **No handoff on nearby player speech** (user decision 2026-09-29): handing off whenever a non-NPC speaks nearby would fire too often. GM detection isn't reliable either: RunUO-style staff name hue 11 is [INFERENCE] for Outlands, hidden staff are invisible, and no staff contact has been captured. Narrower speech triggers (e.g. our character's name being said) are still open.
  - **Refined 2026-10-01 (user): speech hold on automated harvest jobs only.** That decision stands for the overseer walking or running errands in town, where people talk all the time. On a harvest job, though, a character speaking near the agent now holds the job and hands control to the overseer (`harness/speech_guard.py`, `speech_nearby` juncture, urgent). The job resumes only on the all-clear (the juncture acked), or the overseer stops it and talks to the speaker. The worry about firing too often is answered by the filter, measured on the 27 captures: Outlands sends players' titles and guild tags as type-0 "speech" 0.04-0.06 s after every click on them; these, vendors, pets' "(bonded)" and damage numbers are all ignored. Replayed over all captures, the guard fires 4 times, every one real speech ("bank" said by Kanbalt and Tirera at the bank). Speakers who were given the all-clear are ignored for 15 min.
  - **Free text only to answer during a hold (same request).** The allowlist decision above stays for everything else. While a job holds for speech, the overseer may `act say` free text (≤ 120 chars) with the no-reveal word filter (`ctl.REVEALING`). The filter can't rule out every revealing line, which is why it's limited to this one situation, every line is logged as an overseer action row, and the overseer is told to call the human on any sign of staff.
  - **Staff alarm and in-game manner (user request 2026-10-01).** A possible GM gets its own two-tone alarm (`harness/alerts.py`), distinct from the handoff beeps, repeated every 30 s until the `gm_suspected` juncture is acked, so the human at the PC comes to check. Raised by the runner when a speaker has staff hints (GM body, staff-like name, speaking while not on screen) and by the overseer with `ctl alert` when the conversation suggests staff (that judgement often comes after the first line). A held job won't resume while one is open. The overseer prompt (OVERSEER.md §5 "Talking in game") now says how to talk: one short casual line, no volunteered detail, nothing checkable, stall ("sec") when unsure, alarm first.
  - **Laya speech triage: shadow + escalate (user request and decision 2026-10-01).** The user asked whether [Laya](https://github.com/NandhaKishorM/laya) (an encoder that answers typed noul/choice/score questions over text in one forward pass) could decide fast whether a speaker is a GM we must answer or a player we can ignore. Measured zero-shot (`harness/eval_triage.py`: 22 real player lines from the memory DB, 25 GM-style lines we wrote, since no staff speech has been captured), it can't do the ignore half safely:
    - 16 question/checkpoint/prompt variants tried. The best ("greeting, question or request directed at one specific nearby person?", English checkpoint, with the nearby players and recent speech in the prompt) scored GM-style lines 0.06–0.62 and player lines 0.03–0.60. An ignore threshold that holds every GM-style line would let 10/22 player lines through, but that threshold is fitted on the same set and sits on the lowest GM-style line ("you macroing?" 0.058). No margin, and ~2 min unresponsive to a GM gets the character jailed.
    - The attendance-check question separates the explicit checks: at `check ≥ 0.6`, 6/25 GM-style lines ("are you there?" 0.83, "This is a GM, please answer." 0.86, "are you afk?" 0.81, "u there?" 0.76, "what are you doing?" 0.69, "you at your keyboard?" 0.62) and 1/22 player lines ("say 1" 0.69).
    - Laya's README says the same: the base checkpoints only get good after fine-tuning on the domain.

    Decision (user chose from four options): every line still holds the job. Laya's `check` at or above 0.6 is a staff hint (raises `gm_suspected` and the staff alarm as soon as the line is scored, before the overseer has read it). Its verdicts and prompts are kept with how each hold ended (`speech_clear` job events), which is the labeled data for fine-tuning later. Rejected: auto-ignore now (no margin, no real GM data), shadow only (throws away the escalation that works), deterministic filters instead of Laya (the user asked for Laya; they remain an option for ads/emotes).
    - Deployment: `laya-serve` in its own venv (`.venv-laya`, CUDA torch), started by `python harness/triage.py serve` on 127.0.0.1:25970; runners call it over HTTP with stdlib `urllib`, so torch never becomes a harness dependency. When it isn't running, verdicts carry an `error` and the hold works as before (backing off 60 s after a failure).
    - **On the GPU, next to the client (user request 2026-10-01).** It started on the CPU (4 threads, ~0.9 s per line) on the guess that it would compete with the client for the GPU. That wasn't measured, and the GPU turned out to be nearly idle: the client used 727 of 8151 MiB at 0–11 % utilisation. Measured on the GPU with the client running: a 59 ms median forward pass (CPU: 901 ms), +1.8 GB VRAM, scores within 0.003 of the CPU run and no change in what escalates (docs/NOTES.md "Laya speech triage"). fp16 autocast (`LAYA_CUDA_AMP=fp16`), because Laya's default bf16 moves scores by up to 0.073 by its own benchmarks, enough to move lines across the 0.6 threshold. `serve --device cpu` remains for when the GPU is unavailable. Not measured: the client's frame rate.
    - Next: once `speech_clear` events include real GM contact (or enough player lines), re-run `eval_triage.py` with them and fine-tune (Laya's notebook; we have an RTX 5070 locally). Only after that reconsider letting a confident verdict skip the hold.
- **Human-like inefficiency in every runner (user request 2026-09-29):** `harness/humanize.py` `Human`, shared by the runners through `Mover`, adds:
  - lognormal reaction times with fatigue drift
  - per-plan route noise (per 6×6-tile cell, so routes vary but stay straight), pauses and sidesteps; steps at the stock held-key cadence (200/400 ms); doors opened ahead like the client's auto-open. The first version's missed turns into known obstacles were removed on 2026-09-30 (a step the stock client never sends; see the stock-fidelity decision below). **Routes are always run** (user decision 2026-10-01: the client has Always Run on; the earlier 7 % walked routes were dropped). **The client's Auto Open Doors is off** (user decision 2026-10-01): with the per-step re-anchor it would open each door alongside the Mover and shut it again; the Mover's request is the only one
  - occasional cursor hesitation and idle fidgets (backpack open, look at a mobile)

  All of it is stock-client traffic or waiting, and it's seeded. Profile `off` is for deterministic tests. Rationale: server-side detection is behavioural statistics (ANTICHEAT.md §8.3), and optimal routes and uniform click timing are signatures. Details: LUMBER_LOOP.md §13.
- **Harness memory = one SQLite store (user request + Rule 0 clarification, 2026-09-29):** `harness/memory.py`, `harness/data/harness.db` (WAL), gitignored. It holds:
  - every event (the proxy's `MemoryWriter`, batched commits, never blocks the relay; the in-memory ring stays as the hot cache)
  - facet- and z-aware walk evidence derived from `step`/`blocked`
  - harvest nodes and attempts
  - episodes

  Runtime memory is not engineering knowledge and is not committed (AGENTS.md Rule 0, restated). Rejected: JSON files (2D walkmem, whole-file rewrites), Postgres (a service), DuckDB (not stdlib, analytics-first). Details: docs/MEMORY.md.
- **Gating handoff struck from Phase 4 (user decision 2026-09-29):** the harness is not Razor, and PvP is out of scope, so neither `IsRazorBlockedSysMessage` nor the PvP script restrictions apply to it. There is no gating demo and no gating-signal research. The never-reveal-in-game constraint (AGENTS.md constraint 4) is unchanged.
- **First workload: lumberjack → boards → inn-room storage → commodity deed loop** (proposed and accepted 2026-09-29), see [`LUMBER_LOOP.md`](LUMBER_LOOP.md). Approach:
  - learn by one user demonstration; `harness/loop_mine.py timeline` turns the capture into evidence, and `harness/data/loops/lumber.json` is written from it
  - a deterministic routine runner with no LLM in the steady state
  - the LLM only for composing, repairing and post-session reflection
  - optimization of banked boards per active hour, with pacing, breaks and captcha auto-solve as fixed constraints

  Rejected: learning the loop by live exploration. It costs captchas and deaths, and it guesses gump and button ids that one capture gives exactly. Also rejected: LLM calls per cycle (cost and latency, nothing to decide in the steady state).

  **User decisions 2026-09-29:**
  - Venue: Shelter Island first (TestWorth is Young; the capture shows the Young-only welcome gump), the overworld later as a user-initiated move. Leaving Shelter renounces Young permanently, so the agent never travels off the island and never confirms a renounce gump.
  - Logs must become boards for commodity deeds.
  - The return trigger trades carried-goods PK risk against trip overhead, including the 60 s harvest lockout after recall/teleport (LUMBER_LOOP.md §6: `Q* = r·sqrt(2T/h)`). Shelter has no hostile player actions, so `h = 0` there and trips end on breaks. The overworld learns `h` per region from our own data, and a red name means an immediate recall out.
  - Boards bank in the room every trip; a deed is made when the room stock reaches the quantum. **Superseded 2026-10-01 (user decision, LUMBER_LOOP.md §12.5):** boards go into the bank box every trip and the rental room is out of the loop. This drops the rent, the daily room wipe, the hand-secured container and the 60 s post-exit harvest lockout, and works in any town with a banker. It still runs on Shelter, with a fresh Young character (the Shelter bank needs Young).
  - Optimizer autonomy: statistics update automatically within bounds; structural changes are user-approved.
  - **Come back to before the overworld (user note 2026-09-29, LUMBER_LOOP.md §11):**
    - `h` learned from our own exposure and hazard data per region
    - recall out the moment a red name appears
    - whether the Tracking skill is needed to see reds beyond the server's update range
  - **M0 done 2026-09-29:** the demonstration capture `20260929_204225` is mined into `harness/data/loops/lumber.json` and pinned by `harness/test_loop_demo.py` (LUMBER_LOOP.md §12). Findings:
    - 1 log → 1 board, same weight, so convert once per trip
    - deed quantum 5 000 confirmed
    - 60 s lockout after the room exit confirmed
    - real captcha gump `0x00000001`, plus decoy "Captcha" gumps (ANTICHEAT.md §8.13)

    User decision 2026-09-29: prove the loop live first (no deeds, room as daily scratch storage), then optimize. The runner `harness/loop_lumber.py` is offline-proven by `test_loop_lumber.py`. **Live proof ✅ 2026-09-29 22:27 (attempt 2c): 1 full trip, 46 boards stored.** Attempts 2 and 2b first exposed two problems. Mobiles were planned as walls; UOO shoves through them with stamina. And the vendor range for the room menu buttons is 11–12 tiles. They also exposed a third: the captcha submit button id is random per captcha. See LUMBER_LOOP.md §13 and ANTICHEAT.md §8.13.

    **User directive 2026-09-29 (after live attempt 1 died upstairs in the inn, LUMBER_LOOP.md §13):** the harness is only useful with full map knowledge, like the real client: x/y/z land, statics and tiledata. With that it does its own pathfinding and finds its own trees. Plan:
    1. ✅ Decode the Outlands `.uoo` map/tiledata files, read-only from the install dir (docs/MAP.md, `harness/uomap.py`).
    2. ✅ Build a 3D walkability model and port ClassicUO's `Pathfinder.cs` (`harness/pathfind.py`: CreateItemList/CalculateMinMaxZ/CalculateNewZ/CanWalk, z as int, doors passable for planning).
       Evidence in `harness/test_pathfind.py`:
       - all 1249 server-confirmed walk-memory moves are walkable under the model
       - all 20 non-door agent denies upstairs in the inn are walls in the model
       - a 3D route from the inn's upstairs (z 20) down to the innkeeper's floor exists
    3. ✅ `Mover` plans on the map when the facet has geometry (0/1/4/5). The blank rental-room facet 3 falls back to walk memory. The player's facet comes from S2C 0xBF sub 8, now tracked by the world model. Denies are kept z-aware per session.
    4. ✅ Harvest-node discovery:
       - `UoMap.find_trees` finds impassable statics named "tree" in `lumber.json` `harvest.area` (177 on Shelter).
       - Candidates are tried nearest first, with human noise.
       - Harvest memory learns `not_tree` (500489), `unreachable_at` and `depleted_at`.

### Phase 4b — Overworld lumber job under an AI overseer (planned 2026-09-29, night)
User brief of 10 items: overseer AI, PK escape, thieves, rune library, jobs dashboard, monsters,
death recovery, shelf restock, wood values, vendor prices. The plan, status, questions and demos
are in [`ROADMAP.md`](ROADMAP.md); research is in `docs/research/`.

**Decision: the overseer is an omp session driving `harness/ctl.py`** (docs/OVERSEER.md), with
the memory store as the bus:
- runners post junctures
- `ctl wait` blocks until a juncture or a viz chat message arrives; that is how the AI "yields"
  and gets woken
- `ctl say`/`think` show in the viz Overseer tab

Rejected for now: a custom API-driven daemon. It needs an API key and cost decisions; it can
replace omp later on the same bus.

**Standing rule: no PvP.** It draws staff attention, and Heat of Battle (from any aggressive
act against a *player*) blocks recall and entering the room (THREATS.md §6). Hostile players are
escaped, never fought. **Monsters may be fought and looted** (user decision 2026-09-30:
`ctl act attack|loot`, guarded to monsters only; monster combat doesn't cause Heat of Battle,
TRAVEL_DEATH.md).

**Decision 2026-09-30: agent traffic mirrors the stock client's shapes, not just its layouts**
(user request after the detection audit, ANTICHEAT.md §10 A1–A8). Byte-correct packets weren't
enough: the audit found sequences the client can't produce (a context menu without its click,
replies with missing entries, a cursor answered twice, steps into walls, walk rhythm and path
shape unlike a held key). Rule: every agent action reproduces what the client sends for the same
UI gesture, derived from ClassicUO source and checked against human captures, and is refused when
the client couldn't do it right now (entity off screen, no menu open, war mode). Choices:
- A1: client-only cancel plus dropping the client's reply for that spent cursor. Rejected: a
  client-only cancel alone (the client answers it with a C2S cancel, TargetManager.CancelTarget),
  and leaving the cursor up (the human's next click would be a second reply).
- A2: 3 s confirm timeout, a 5 s late-confirm window, the stock 5-unconfirmed cap. Rejected: a
  proxy resync to heal the ladder (server-visible, §8.11).
- A8: stock cadence inside a stretch, texture between stretches; route noise per 6-tile map cell
  plus `nav.straighten` (measured on 24 Shelter routes: ~20 % heading changes, human 21 %).
  Rejected: a turn-penalty A* (8× the search states; not tried, since the above already matches).

## Data backups (decided 2026-10-01)

Gitignored runtime data (the memory store, captures, the Ghidra project) had no copy off this
machine. Hourly `harness/backup.py` to the NAS share, as a Task Scheduler job (docs/NOTES.md
"Backups").
- DB: SQLite online backup API, not a file copy. Copying `harness.db` while the proxy writes
  would miss WAL contents or tear pages. Rejected: `VACUUM INTO` (rewrites the whole DB with
  more CPU for no gain at this size).
- Timestamped, deduplicated, gzipped DB snapshots with 48 h hourly / then daily retention, so a
  corrupted or wrongly edited store can't overwrite the only good copy.
- robocopy for file trees: native, incremental, network retries. Rejected: a Python tree copy
  (re-implements robocopy), File History / Windows Backup (whole-profile, not repo-aware).
- UNC path, not `F:`, because scheduled tasks don't see interactive drive mappings.

## Fixes from the first Hackworth overseer shift (decided 2026-10-01)

The user approved items 1–4 of the overseer's fix plan. Item 5, auto-clearing broadcast
chatter, is skipped for now: every speaker still holds a harvest job.
- **World model prunes like the stock client** (docs/WORLDMODEL.md §7; fixes ANTICHEAT.md A12).
  - It applies the decompiled `World.ProcessDeletes` range rule (S2C 0xFF sub 5) at the
    server's 0xC8 range (18) on every self move and on sub 5. It also removes mobiles on
    deaths (0xAF/0xDEAD) and facet changes, and takes their children with them.
  - Dropped mobiles go to `world.last_seen`. Only `npcs`, `goto` and the viz read it, never a
    guard (user decision 1: keep a last-seen store).
  - Chosen over a "stale" flag on live mobiles because every guard that reads `world.mobiles`
    becomes correct with no per-guard change.
  - Vitals-only packets update only mobiles the model already has (ClassicUO `World.Get`), so
    a late packet can't bring a removed mobile back.
  - `harness/audit_ghost_targets.py` is the audit rule for agent packets at serials the client
    had dropped. Run it on new captures next to the "serials the server never sent" audit.
- **Who attacks us:** `world.swings` (latest S2C 0x2F per attacker), `status.attackers`, and
  `age_s` per mobile.
- **Hunt task** (`harness/loop_hunt.py`, docs/HUNT_LOOP.md, `ctl run hunt`): a programmatic
  task for NPD mongbats.
  - Thresholds are CLI arguments (user decision 3): `--heal-at 0.75 --leave-at 0.60
    --leave-multi-at 0.80 --mana-reserve 22 --rest-to 0.95`.
  - Targets come only from the live world model plus the ctl attack guard, attackers first.
    State is re-read before each cast and each target answer; a stale target gets the stock Esc
    `0x6C`.
  - Combat rules and stock packet sequences live in `harness/combat.py`, shared with
    `ctl act attack/target/loot/cast`, so the two can't drift. ctl's bytes are unchanged.
  - It replaces the overseer's improvised fight loop, which let Hackworth fall to 2/96.
- **Lumber runner:**
  - The hatchet is found worn, or else the shallowest one in the backpack's bags (containers
    are opened outermost first).
  - A war-mode creature busy fighting someone else (latest swing at another mobile, ≤ 10 s old,
    none at us, outside its strike range) is `watch`, not `flee`.
  - A creature threat triggers an escape: the runner walks 2 tiles beyond the creature's flee
    radius and goes on with the next tree out of its reach. It stops if the creature still
    follows, after 3 escapes per trip, on damage, and at once for players and reds. Escape on
    foot for now; recall stays the PK answer (ROADMAP 2).
  - An abort during the harvest converts the carried logs first, unless that's unsafe.
  - A break that comes due ends the trip at the bank (convert, store, exit 0), then the
    overseer runs `ctl break`.
  - No weight trigger: logs and boards weigh ~0.025 st each.


## Risks

- **Protocol drift**: Outlands patches frequently (client is days old at research time). Parser must be tolerant of unknown packets (log-and-forward) with a packet-ID registry that's easy to update.
- **Custom login/auth**: the OutlandsID HTTPS handshake is not yet mapped at byte level; proxy only needs the post-auth game connection, but settings/login responses may bind the session to the original destination — validate in Phase 1.
- **Behavioral statistics**: even perfect protocol emulation can be detected by play patterns; pacing, session length, and task selection are the mitigation (§8 rules).
- **Staff attention on Test Shard**: keep activity non-disruptive to avoid drawing staff notice (detection risk).

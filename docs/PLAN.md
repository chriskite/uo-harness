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
  - Break length 3–5 min, jittered (user decision 2026-10-02; it was 15–30 min, too long). A gap
    of at least 3 min without agent actions counts as a break taken.
  - At most 10 h of agent-active time per local calendar day (user decision 2026-10-06; it was 8 h, and the overseer kept ending shifts well short of it). Paused and break time don't count.
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
  - **Shard lines don't hold (user approval 2026-10-07, Seer-6 incident 1).** Juncture 532 held a lumber job 24 s on a blue player's "*hiking to destination*". That line, like `*regens*`, `*inspired*`, `*shield bash*` and `*area taunt*`, is a status line the shard speaks over a player: type 0, wrapped in asterisks, system hue 946. 23 of the first 76 holds were such lines. `speech_guard.shard_line` now skips asterisk lines in hue 946, the few fixed ones seen in other hues (`*herds followers*`, the poison stages) and `**…**` region banners, like pet commands: still in `context`, and a speaker with staff hints still holds. Typed emotes come in the player's own hue and still hold. Plain lines in hue 946 (NPCs, title echoes) aren't touched. Risk: a GM typing an asterisk line in hue 946 would pass ([INFERENCE] player speech defaults to another hue; an attendance check is a question).
  - **Staff alarm and in-game manner (user request 2026-10-01).** A possible GM gets its own two-tone alarm (`harness/alerts.py`), distinct from the handoff beeps, repeated every 30 s until the `gm_suspected` juncture is acked, so the human at the PC comes to check. Raised by the runner when a speaker has staff hints (GM body, staff-like name, speaking while not on screen) and by the overseer with `ctl alert` when the conversation suggests staff (that judgement often comes after the first line). A held job won't resume while one is open. The overseer prompt (OVERSEER.md §5 "Talking in game") now says how to talk: one short casual line, no volunteered detail, nothing checkable, stall ("sec") when unsure, alarm first.
  - **Laya speech triage: shadow + escalate (user request and decision 2026-10-01).** The user asked whether [Laya](https://github.com/NandhaKishorM/laya) (an encoder that answers typed noul/choice/score questions over text in one forward pass) could decide fast whether a speaker is a GM we must answer or a player we can ignore. Measured zero-shot (`harness/eval_triage.py`: 22 real player lines from the memory DB, 25 GM-style lines we wrote, since no staff speech has been captured), it can't do the ignore half safely:
    - 16 question/checkpoint/prompt variants tried. The best ("greeting, question or request directed at one specific nearby person?", English checkpoint, with the nearby players and recent speech in the prompt) scored GM-style lines 0.06–0.62 and player lines 0.03–0.60. An ignore threshold that holds every GM-style line would let 10/22 player lines through, but that threshold is fitted on the same set and sits on the lowest GM-style line ("you macroing?" 0.058). No margin, and ~2 min unresponsive to a GM gets the character jailed.
    - The attendance-check question separates the explicit checks: at `check ≥ 0.6`, 6/25 GM-style lines ("are you there?" 0.83, "This is a GM, please answer." 0.86, "are you afk?" 0.81, "u there?" 0.76, "what are you doing?" 0.69, "you at your keyboard?" 0.62) and 1/22 player lines ("say 1" 0.69).
    - Laya's README says the same: the base checkpoints only get good after fine-tuning on the domain.

    Decision (user chose from four options): every line still holds the job. Laya's `check` at or above 0.6 is a staff hint (raises `gm_suspected` and the staff alarm as soon as the line is scored, before the overseer has read it). Its verdicts and prompts are kept with how each hold ended (`speech_clear` job events), which is the labeled data for fine-tuning later. Rejected: auto-ignore now (no margin, no real GM data), shadow only (throws away the escalation that works), deterministic filters instead of Laya (the user asked for Laya; they remain an option for ads/emotes).
    - Deployment: `laya-serve` in its own venv (`.venv-laya`, CUDA torch), started by `python harness/triage.py serve` on 127.0.0.1:25970; runners call it over HTTP with stdlib `urllib`, so torch never becomes a harness dependency. When it isn't running, verdicts carry an `error` and the hold works as before (backing off 60 s after a failure).
    - **On the GPU, next to the client (user request 2026-10-01).** It started on the CPU (4 threads, ~0.9 s per line) on the guess that it would compete with the client for the GPU. That wasn't measured, and the GPU turned out to be nearly idle: the client used 727 of 8151 MiB at 0–11 % utilisation. Measured on the GPU with the client running: a 59 ms median forward pass (CPU: 901 ms), +1.8 GB VRAM, scores within 0.003 of the CPU run and no change in what escalates (docs/NOTES.md "Laya speech triage"). fp16 autocast (`LAYA_CUDA_AMP=fp16`), because Laya's default bf16 moves scores by up to 0.073 by its own benchmarks, enough to move lines across the 0.6 threshold. `serve --device cpu` remains for when the GPU is unavailable. Not measured: the client's frame rate.
    - **No GM wording to anchor synthetic data (searched 2026-10-04).** The user asked whether synthetic GM and player lines could fine-tune Laya. The Discord capture (97k messages) has no message quoting what staff say in a check, only players' reports that unanswered gatherers get jailed (ANTICHEAT.md §3). So synthetic GM lines would encode our guesses: usable to escalate sooner, never to skip a hold. The capture is good seed material for the player side and its hard negatives ("u there?" between friends, ads, pet commands).
    - **Fine-tuned for escalation, deployed as v2 (user request 2026-10-04).** Fine-tuning serves escalation only: a higher `check` raises `gm_suspected` sooner; every line still holds the job. How to rebuild/train/evaluate/roll back: docs/NOTES.md "Laya speech triage".
      - *Evaluation frozen first.* The zero-shot baseline (stock checkpoint, hub commit 55cf4c4) was saved before any training. The 47 `eval_triage.py` lines plus **104 held-out real player lines** (`triage_data.py`: every text an eval speaker said, the one real conversation aimed at us, 30 % of the other texts by hash, closed under near-duplicates; 123 of the 215 distinct real texts) never enter training, not even as context, and synthetic or Discord lines within difflib ratio 0.85 of any of them are dropped.
      - *Training set* (`harness/triage_data.py`, 1057 prompts in exactly the `state_text` format; counts in the table): real in-game player lines from the memory store (92 distinct texts, ≤3 contexts each, re-used under another of our names when seen once), Discord messages as a bounded 25 % of the player side (second-person questions skipped), synthetic hard negatives (friends pinging each other with context naming them, trade ads, guild chatter, pet commands, 'say 1'-style group counts, Turkish/French/Portuguese/Czech/Japanese chatter, ad cycles), synthetic GM checks aimed at us (English, formal/terse/typo'd/lowercase, with and without our name, after silence, after our own line, mid-chatter, speaker on screen or hidden), the same wording aimed at someone else, and players talking to us without checking on us. Near-duplicates within a class are dropped (ratio 0.9). Our names vary (never the eval's TestWorth).

        | source | check | direct | rows |
        |---|---|---|---|
        | real (memory store) | 0 | 0 | 221 |
        | real, addressee unknown | 0 | – (check only) | 22 |
        | discord | 0 | 0 | 149 |
        | hardneg (synthetic) | 0 | 0 | 206 |
        | gm (synthetic) | 1 | 1 | 276 |
        | decoy (gm wording aimed elsewhere) | 0 | 0 | 75 |
        | toyou (players talking to us) | 0 | 1 | 108 |

      - *Training* (`harness/triage_train.py`, Laya's supported recipe from its fine-tuning script: RLCD + cross-entropy, AdamW 2.5e-5/1e-4 cosine, gradient checkpointing, noul temperature refitted on a 10 % calibration split): top 6 of 28 encoder layers + decision head (99.8M of 421M parameters), frozen weights kept in fp16, bf16 autocast, 4 epochs of 1885 items, micro-batch 8 × accumulation 2. 288 s on the RTX 5070 Laptop next to the running client, peak 2756 MiB allocated / 3068 MiB reserved under a 3.2 GB cap.
      - *Four runs* (each data change below was prompted by the previous run's held-out errors, so the held-out set guided development and is not fully blind for run 4):

        | run | change | layers / epochs | noul T | GM-style caught (of 25) | frozen player alarms (of 22) | held-out real player alarms (of 104) |
        |---|---|---|---|---|---|---|
        | stock | – | – | 1.98 | 6 | 1 ("say 1") | 5 |
        | 1 | first set (a few non-English GM lines) | 4 / 3 | 1.32 | 9 | 2 | 15 |
        | 2 | GM lines English only; more Turkish/French ads, more lines to us | 6 / 6 | 4.69 | 10 | 2 | 12 |
        | 3 | + back-and-forths with us (our reply in the context) | 6 / 4 | 1.63 | 7 | 1 | 11 |
        | 4 | + ad cycles (one player's 2–5 ads in a row) | 6 / 4 | 3.12 | 10 | 1 ("Tek basina…" EoB ad) | 4 |

        Runs 1–3 alarmed on held-out Turkish/English EoB recruitment cycles and on the real conversation aimed at us (Da Mountain Man, session 40). Run 2's loss rose again after epoch 3 (train CE 0.30 → 0.56 at σ ≈ 0.22), so later runs stop at 4 epochs.
      - *Run 4 vs stock, per line* (`eval_triage.py --compare`): caught now "Hello?" 0.49→0.93, "hello TestWorth" 0.25→0.71, "you macroing?" 0.20→0.70, "\*waves at TestWorth\*" 0.13→0.84; the six explicit checks stay caught (five rise, e.g. "are you there?" 0.83→0.99; "This is a GM, please answer." 0.86→0.80). Player lines: "say 1" 0.69→0.53 (no longer alarms), two held-out EoB ads drop below 0.6, "FREE Leather or Bone Aromor" 0.57→0.21, most held-out ads and chatter drop 0.2–0.36. **Worse:** frozen "Tek basina dungeon farm yapma!…" 0.46→0.76 (new alarm), held-out "ah" (Da Mountain Man, to us) 0.43→0.86 (new alarm); GM-style lines that aren't explicit checks drop further ("please respond" 0.32→0.22, "anyone home" 0.45→0.13, "Can you say the word apple for me?" 0.20→0.07); `direct` now fires on lines to nobody ("bank" 0.04→0.82, "[EoB] Regular Dungeon Farms!" 0.10→0.89), still shadow-only. Held-out alarms that remain: one EoB ad and three Da Mountain Man lines.
      - **Weak evidence, said plainly:** the 25 GM-style eval lines are ours too, and so is every positive in training, so a gain there shows the model learned our idea of a check, not Outlands staff. The hard check is real held-out player lines not escalating: 5 → 4 alarms of 104, a one-line margin on a set that guided runs 2–4.
      - **Decision: deployed** (the rule set before training: player false escalations on real held-out lines must not increase and GM-style catches must improve; both hold). `triage.CHECKPOINT` points at `models/laya-triage`, `VERSION` v2. It takes effect at the next `triage.py serve`; the live service was found stopped at 12:12 that day and left to the user. Way back: `triage.py serve --checkpoint stock`, or `CHECKPOINT = STOCK`.
      - *Stock pinned.* An unpinned `laya-serve` start that day downloaded a newer hub commit (7b928d8, 846 MB) that nothing here measured; `triage.py serve` now sets `LAYA_REVISION` to 55cf4c4 (laya's own reviewed pin), the baseline's commit and the fine-tune's base.
    - Next: once `speech_clear` events include real GM contact (or more real player lines), add them to the held-out set first, re-run `eval_triage.py` on v2 vs stock, and retrain with them. Only after that reconsider letting a confident verdict skip the hold.
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
  - Boards bank in the room every trip; a deed is made when the room stock reaches the quantum. **Superseded 2026-10-01 (user decision, LUMBER_LOOP.md §12.5):** boards go into the bank box every trip and the rental room is out of the loop. This drops the rent, the daily room wipe, the hand-secured container and the 60 s post-exit harvest lockout, and works in any town with a banker. It still runs on Shelter, with a fresh Young character (the Shelter bank needs Young). **That bank decision is itself superseded 2026-10-04: "Lumber trips end in the rental room, not at a bank" (below).**
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

## Two computers: hand the memory store off (decided and built 2026-10-04)

User request: play on a desktop for a while and find its changes on the laptop afterwards. The two
computers aren't used at the same time. Built: `harness/dbhandoff.py` (docs/NOTES.md "Two
computers"), with `SETUP.md` for a coding agent setting up the second computer.
- **One holder at a time, with the store passed through the NAS share.** `push` uploads a verified
  snapshot as a new generation and releases the store; `pull` installs it and records the new
  holder in `handoff/owner.json`. Pushes and pulls are refused when they would fork or overwrite
  history; `--discard-local` and `--force` override them, and the replaced store is kept.
- Rejected: **the live store on the share.** WAL needs every connection on one host (shared
  memory in `-shm`), and SQLite doesn't support WAL over a network filesystem. The proxy commits
  every 0.25 s.
- Rejected: **a sync client (OneDrive, Syncthing) on `harness/data/`.** It copies `harness.db`
  and `-wal` at different moments, which tears the store. It also has no notion of a holder.
- Rejected: **merging two diverged stores.** Autoincrement ids collide in `sessions`, `events`,
  `episodes`, `chat`, `junctures`, `job_events`, `knowledge` and `prices`. `knowledge` supersede
  links point at those ids, `walk_moves`/`harvest_nodes` counters would need adding, and the `meta`
  cursors conflict. Preventing forks is cheap and merging isn't, so forks are refused.
- **Backups follow the holder.** `backup.py` skips the store's snapshot on a computer that doesn't
  hold it. Otherwise a computer catching up on a missed hourly run would upload its stale store as
  the newest snapshot.
- **The Laya checkpoint travels with the store (user request 2026-10-04).** The deployed triage
  model (`models/laya-triage`, 843 MB) isn't in git and can't be retrained bit for bit, so `push`
  uploads it to `handoff/model/` when its manifest hash changes and `pull` installs it, verified per
  file, keeping a replaced one as `.prev`. `backup.py` also copies `models/` (additive, `/XO`).
  Rejected: retraining on each computer (needs `discord.db`, which stays on one computer, and gives
  a different model) and git LFS (a GitHub storage quota for an 843 MB binary that changes on every
  retrain).
- **The Telegram bridge config travels with the store** (user request 2026-10-04). The bridge
  belongs on the computer that holds the store, and a push needs the bridge stopped, so the token
  never has two pollers. The cost: the bot token sits on the NAS share in plain text. That's
  accepted on the private LAN share; if it leaks, BotFather `/revoke` invalidates it.
- **No machine paths in code (user request 2026-10-04, desktop setup).** The desktop's home dir
  is `C:\Users\Chris Kite` (with a space), not the laptop's `C:\Users\chris`. Every script now
  derives the repo from its own location and runs Python via `sys.executable` (Python) or
  `UO_PY`/`python.exe` on PATH minus the Store stub (`.ps1`/`.cmd`); `start_proxy_nat.ps1` passes
  its interpreter to the elevated NAT worker as `-Python`, since a UAC child doesn't inherit the
  environment. Rejected: installing under an identical path on both computers (the old SETUP.md
  advice): it ties the repo to one Windows user name and breaks silently on the next machine.

## Overseer chat on Telegram (decided 2026-10-02)

User request: the overseer chat and notifications on a Telegram bot. `harness/telegram_bridge.py`
(docs/OVERSEER.md §8) is one more party on the store bus: overseer messages and the junctures
that wake `ctl wait` go to one paired private chat; text from that chat becomes `user` chat rows,
so the overseer, ctl and the viz are unchanged.
- Long polling (`getUpdates`), not a webhook: a webhook needs a public HTTPS endpoint into this
  PC. Stdlib `urllib`, not python-telegram-bot: two API calls don't justify a dependency.
- Its own cursors in `meta`, advanced per row after sending: restarts and outages neither drop
  nor repeat; a first run starts at the newest rows.
- Forwarded by default: what the user would act on (overseer messages, waking junctures) loud,
  viz lines and a task's end silent. Thoughts and actions are opt-in flags: a shift posts dozens.
- One paired chat only; other senders are ignored, since anyone can find a bot by name.
- Rejected: Telegram commands that act directly (ack, stop). Everything goes through the
  overseer, which applies OVERSEER.md §5; the human's own controls stay the viz and the client.

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
  - **Revised 2026-10-07 (user): run until it gives up.** "Dan is still not running far away enough
    from monsters; he's giving up and recalling out too soon. If you run far enough from a monster
    it 'rubber bands' back to its spawn point, and you can continue chopping." Live that evening an
    orc came back 3 s after a single 11-tile walk-away (witcher_177) and a third escape ended a trip
    (witcher_188), both by recall home. Now an escape runs in legs until each creature is out of
    view or 19+ tiles off and not closing in, watches 2 s, and chops on; it records each shake-off
    (`leash` job event) so the chase distance gets measured. Still home: a creature that keeps pace
    (in flee range, or a ranged one in reach, after a leg), 6 legs / 200 steps without shaking it,
    a 4th escape from the same creature, 8 escapes in a trip (was 3), and the damage rules
    (low hits, two attackers, two hits on one walk-away, a hit from next to us). Rejected: a fixed
    leash distance (unmeasured on Outlands); dropping "kept coming" (the creatures at our heels
    that killed Dan on 2026-10-05 still need the recall). Details: LUMBER_LOOP.md §13 "Run until it
    gives up".
  - An abort during the harvest converts the carried logs first, unless that's unsafe.
  - A break that comes due ends the trip at the bank (convert, store, exit 0), then the
    overseer runs `ctl break`.
  - No weight trigger: logs and boards weigh ~0.025 st each.

- **Healing subroutine (user decision 2026-10-02):** `harness/healing.py` makes the choice for
  both `ctl act heal` and the hunt runner, so the two can't drift.
  - A heal potion whenever one can be drunk: not at full health, and 10 s since the last drink.
    A server "wait" refusal (cliloc 500235) falls back to a spell in the same call.
  - Otherwise Heal or Greater Heal by the missing hits. Greater Heal from the mana break-even
    (Heal's average × 11/4, from the wiki Magery formulas: 19 missing at Magery 60), else Heal.
    The threshold can be overridden with `--gheal-min-missing`.
  - Bandages are out: Hackworth has no Healing skill (user, chat#919: +4..+9 each).
  - Resting outside the hunt uses spells only (my call): mana regenerates for free there, while
    potions cost gold.
- **Hunting dashboard (user request 2026-10-02):** the viz Jobs page gets a Hunting job next to
  Lumber, tracking mobs killed, gold looted and XP earned (docs/VISUALIZER.md §2.4).
  - XP = Outlands mastery-chain experience = creature gold value × damage share (user note +
    wiki). The server shows XP only in the chain's gump; reading that would mean sending
    `[MasteryChain` or opening the gump every kill, which a player doesn't do. So the runner
    records the gold the corpse held as each kill's XP [INFERENCE: solo kills]. Kills never looted
    count as unknown, not guessed.
  - Kill/gold/XP figures come from the job events, not the visit rows: events are written at
    once, so a stopped run's last visit (no row) still counts in the totals; the rates use only
    events inside recorded visits over their duration.


## Player houses and mounted pace (2026-10-02)

- **Houses go into the walk rules as the client places them.** Each 0xF3 multi expands into its
  multi.mul pieces (`uomap.multi_components`) inside `pathfind.Walkers.get`, so the proxy's z
  tracking, the Mover and ctl all see them. Rejected: learning houses only from server denies (walk
  memory). It costs a deny burst per house, which no client produces, and a house can be placed or
  removed at any time. Only houses in view are known. Persisting them for long-route planning is
  open.
- **Mounted pace = the stock mounted cadence** (user decision 2026-10-02, after their own ride
  confirmed Outlands takes 0.1 s steps; ANTICHEAT.md A13). One mount state,
  `StateStore.mounted()` from the server's layer-0x19 equip, drives both the proxy floor and the
  agent cadence, so they can't disagree. Rejected: a per-task `--mounted` flag (it goes stale on
  a dismount, and a stale "mounted" on foot is a Speedhack signature).

## Red sighting: recall at once (decided 2026-10-02; recall-on-sight built the same day)

**Status:** items 1–3 (readiness, trigger, recall) are built in `harness/escape.py` + `loop_lumber.py`,
plus `ctl act recall`. They were proven live with TestWorth's runebook and rune tome on the Test
Shard (docs/LUMBER_LOOP.md §13, docs/NOTES.md "Runebook and rune tome gumps"). Hackworth needs a
book before he can work off Shelter.

Still open:
- the run-away behaviour (item 4); its no-recall fallback, the guard flight, is built (below)
- path-based player ETAs and Tracking (item 6)
- the hunt runner: dungeons block recall, so its red rule is unchanged

Evidence: the Terran PK (docs/NOTES.md "PK death in the Terran wilds") and the one live runebook
recall (TestWorth, 2026-09-30 18:59, `logs/session_20260930_182751.jsonl`): double-click the
runebook to gump 48 ms; the rune's Recall button to the server's power words 46 ms; arrival
(sound 0x01FC, world resend) **2.09 s after the button**. Cast time 2.0 s, like the spellbook cast
(cursor 2.003 s after the cast started, same session). Bastet needed **4.63 s** from his first
`0x78` to his first hit. His route around the terrain was ~27 mounted steps for an 18-tile
Chebyshev gap, with pauses; he set us as his combatant (S2C 0xAA, "Bastet is attacking you!")
at 2.85 s.

So a recall started within ~2.4 s of sight lands before the first hit. Stopping, the current
`Unsafe` abort, left the character standing until death. The worst case in THREATS §3.4 (an
instant straight approach, about a 1.7 s first hit) still beats a recall from a standstill. A
recall is the only action with a chance, so it wins.

Policy (lumber and hunt runners, overseer):
1. **Readiness gate off guarded ground:** a recall source in the pack and a way to cast it. That
   is a runebook with a home rune, **or a single marked rune**: Recall's target cursor comes at the
   end of the 2.0 s cast, and the harness answers it at once, so the timing matches the book's
   button. Plus Recall in the spellbook with reagents or the spellstone, or recall scrolls / book
   charges. Missing → the runner refuses to start outside guards.
   - **Banking** stays on the return trigger, not a fixed cap: `Q* = r·sqrt(2T/h)` (LUMBER_LOOP §6;
     by value `V* = ρ·sqrt(2T/h)`, ECONOMY §6). The PK hazard `h` per region now has its first
     data point. User 2026-10-02: the opportunity cost of banking trips is high, so don't bank
     more often than the trigger says.
2. **Trigger:** any notoriety-6 mobile in view, a 0xAA or "… is attacking you!" naming a player,
   or player damage. For reds, no ETA test: the 0.6 s estimate (strike range 12, straight line)
   was 8× off, and every red in view is in range anyway.
3. **Action:** recall at once, **no added pause** (user 2026-10-02).
   - No salvage conversion, no war mode, never attack back (Heat of Battle would block recall;
     auto-defence swings don't count).
   - The game holds you still while casting (user 2026-10-02), so the runner just waits for the
     cast.
4. **Interrupted (500641) and alive:** ride away and recast.
   - **Hamstring is the PK opener because it ends running.** Live: "Their attack hamstrings
     you!" with the first hit, "You are no longer hamstrung." 3.08 s later. The wiki says stamina
     goes to 0, forcing a walk (mounted walk 5 tiles/s vs the PK's 10). While hamstrung, running
     is pointless: recast at once.
   - Running away is only worth it before the first hamstring or after one wears off. It's an open
     design problem: line of sight, terrain the PK's path must go around, toward guards.
   - With no recall source at all: ride to the nearest known guard edge.
   - A "5…1" countdown over an attacker = an explosion potion ~5–6 s after "5".
5. **After landing:** urgent `pk_escape` juncture with the killer's name and the spot. Wait out the
   60 s harvest lockout, then walk 5 steps (stationary penalty). The overseer keeps the character
   away from that region for ≥ 30 min and records the PK in `know`.
6. **Detection fixes:**
   - Treat S2C 0xAA and "X is attacking you!" as under-attack signals. The attacker sends no
     `0x2F`.
   - Compute player ETAs from the planned path (Mover.plan), not Chebyshev distance × 0.1.
   - Investigate why Tracking (murderers) never registered Bastet.

Recall inside a dungeon is blocked (except near golden gates), so the hunt runner keeps its walk to
the exit for monsters in the NPD. Reds can't reach the Shelter NPD.

**Hunting by recall (2026-10-03, user: Urukton Bluffs for Hackworth's Magic Reflect scroll).** The
hunt runner got `--enter-recall/--leave-recall` (docs/HUNT_LOOP.md "Recall in, recall out") rather
than a new runner: the fight, heal, loot, staff and penalty rules are the NPD's, only the way in and
out changes. Every leave recalls home (the rune's landing is by a golden gate, where recall
works); a refused recall walks to `--recall-spot` (the arrival) and tries again, rather than
guessing gate positions. Hostile players: the lumber red escape's triggers (a red anywhere in
view, "X is attacking you!") and its watchful waits, so the reaction isn't a tick late. Unlike
the lumber runner it goes back in after `--pk-wait` (user scenario: recall home, bank, re-enter);
the gold is banked each time home (`--bank-gold`) so a death costs only the current visit's loot.

### Guard flight (built 2026-10-02)

Item 4's "ride to the nearest known guard edge" as the backup for a failed recall (`escape`
fails or raises `RecallError`) or no book (`--recall off`). Before this the runner stopped where
it stood. Code: `harness/guards.py`, `LumberLoop.flee_to_guards`, `nav.any_of`,
`Mover.walk_to(goal_fn=, urgent=True)`, the `guard_points` table (docs/MEMORY.md).

- **Where to run:** guard zone boundaries aren't in the client data. Goals are learned guard
  points (radius 0) plus the client's bank markers (`Banks_and_Healers.xml`, radius 2) minus
  lawless towns (`LAWLESS_TOWNS`: Corpse Creek). All lie within 250 tiles; with the attacker
  within 12 tiles, places nearer to it than to us are dropped, unless that drops them all. A* over
  all goals at once (`nav.any_of`, heuristic = min).
- **Learned points are enter notices only, at the tile we stood on.** The approved plan also used
  "You have left…" (500113) and extrapolated one tile inward. The live probe (docs/NOTES.md "Guard
  zone notices") showed the notices lag (3.3 s) and skip crossings, so a 500113 doesn't say where
  the inside is. One extrapolated point, (1477,1497), was where the first flight round stopped
  without a notice. Backfill: 32 enter notices → 25 points (Terran, Prevalia, Horseshoe Bay,
  Shelter, …).
- **Arrival counts as safe, notice or not.** The plan dropped a goal reached without a 500112
  and walked on. With a notice that may never come (none for ~5 min over ~16 crossings), that
  walks away from guarded ground. The flight stops on the 500112 if it comes on the way, or on
  arrival. `confirmed` in the juncture says which.
- **Pacing:** urgent = no wander sidesteps, after-step pauses or reading waits. Run unless
  stamina ≤ 1: that's the stock client's own rule (`PlayerMobile.Walk`: `Stamina <= 1 && !IsDead`
  → `run = false`), the hamstring case.
- **In town:** "guards" once if a hostile player is within 12 tiles (wiki: a PK attacking a blue
  in a guard zone is guard-whacked; THREATS line 81). Then `guard_flight` job event + urgent
  `pk_escape` (`method: guards`) + stop (Unsafe), as the recall does.
- **Rejected:** a "latest notice was an enter, so we're already inside" shortcut. Notices skip,
  so it can be minutes stale.
- **Live check (TestWorth, Prevalia, Test Shard):** fake red, `--recall off`, from (1481,1505)
  between the two Prevalia zones: 10 steps, all runs, to the learned point (1483,1515) in 4.42 s;
  `pk_escape` `method: guards`, `called_guards: false`, `confirmed: false`.

## Self-optimizing lumber (decided and built 2026-10-02)

User request: the lumber loop and its overseer should optimize gold/hour by themselves over the
coming weeks, exploiting and exploring lumber spots, and later choose the hatchet colour from its
price, durability and tool bonus against the risk of losing it to a PK; logs/hour stands in for
gold/hour until colored-wood prices are known. The character's skill and Harvest Aspect grow
meanwhile. Built: `harness/lumber_opt.py`, `ctl lumber …`, spots in `harness/data/lumber_spots.json`
plus the store, hatchets in `harness/data/hatchets.json`; the model is docs/LUMBER_LOOP.md §6
"Built 2026-10-02".

- **Thompson sampling over spots, run by the overseer per stint.** `ctl lumber plan` draws once
  per eligible spot and the overseer runs the winner's command. It explores in proportion to how
  likely a spot is to be the best, needs no tuning constant, and varies the routine, which
  ANTICHEAT.md §8.3 wants anyway. Rejected: ε-greedy (explores bad spots as often as promising
  ones, needs an ε schedule); UCB (deterministic, so the same spot order every day; a fixed pattern
  is a bot signature); an LLM choosing spots by reading the stats (not reproducible, no
  uncertainty accounting). The runner stays a one-spot executor and the plan is a pure function
  of the store, so the overseer, a future daemon or a human run the same decision.
- **A structured model, not a black-box reward per spot.** A spot's rate is split into what
  belongs to the place (field rate λ, overhead T, sighting rate) and what belongs to the character
  (success chance from skill and tool bonus, wiki formulas). Each trip's chopping time is rescaled
  to today's success chance. Skill rises for weeks; a plain per-spot logs/hour average would
  favour whichever spots happened to be visited recently and force re-exploring everything as the
  skill grows. Harvest Aspect isn't in the formula; recency weighting (half-life 14 days) absorbs
  it and other drift (competition, patches, PK habits).
- **The trip size is the §6 return trigger, decided per spot from its learned hazard.** Hazard =
  sightings/hour × P(death | sighting): sightings arrive long before deaths, which are rare.
  This replaces fixed `--logs-per-trip` values; the user's 2026-10-02 note (banking trips are
  costly, don't bank more often than the trigger says) holds.
- **2026-10-03 (user decisions): the trip size is a renewal-reward rate over three competing
  hazards** (LUMBER_LOOP §6 "trip size"). The first model credited a dying trip with Q/2 banked
  and charged deaths = h·Q/λ, which exceeds 1 for long trips; it also capped Q at what a 60-min
  stint can chop. Now: a death banks nothing and loses every unblessed item at full price (all
  hatchets carried, reagents; nothing when Young); a trip a threat ended (recall, guard flight,
  creature stop) banks what it carries; a theft takes a share f of the load. Each hazard is
  learned per spot with shrinkage to a pooled rate (the sighting × P(death | sighting) path is
  the death prior). Q in 200…10 000 logs, capped by weight, no stint cap; `--timeout` scales with
  the trip. Rejected: keeping the first-order formula with a clamp on deaths (still wrong for
  sent-home trips and theft), and Monte Carlo per Q (the integrals are closed form).
- **Every trip is evidence, aborted ones too.** Until now only trips that reached the bank wrote
  an episode row, so the spots where trips get cut short looked better than they are. The runner
  now writes the row in a `finally` with `outcome` and `why`.
- **New spots: proposed from the map, approved by the overseer.** `ctl lumber discover` finds
  tree-dense areas near bank markers; they start as `candidate` and enter the plan when the
  overseer approves them after a look at `ctl map`. This user request grants the system the spot
  choice; the LUMBER_LOOP §7 rule that a venue change needs the user stays for structural
  changes (new loop states, where to deed). Rejected: auto-activating every map candidate (a
  dungeon mouth or a walled garden would cost deaths and wasted trips before the data caught up).
- **Witcher groves up to 200 tiles from the rune (user, 2026-10-03).** The first search looked only
  21 tiles around each rune, and 212 of 360 runes had too few trees within that. Now one grove per
  rune, up to 200 tiles away, with a walk of at most 300 tiles from its landing. Windows sit on one
  global 7-tile lattice and are ranked across all runes (most trees, then nearest), so a grove two
  runes reach goes to the nearer one instead of to whichever rune came first, and a rune whose best
  grove is taken gets its next best. Tree counts come from a summed-area table (numpy, already a
  ctl dependency through knowledge.py); the route check uses the runner's planning budget (30 000
  expansions, was 60 000), so a candidate's walk is one the Mover can plan. A discovery replaces its
  own unreviewed candidates (the 30-candidate pending cap is gone: with replacement it would have
  dropped good groves). The overhead prior now counts the walk from the rune into the grove.
  Rejected: several groves per rune (adjacent windows of one forest would crowd the list), and
  ranking by tree count net of the walk (a 300-tile walk is ~2 min against an hour's trip).
- **All 125 Witcher candidates not named after monsters approved at once (user, 2026-10-03).**
  This overrides the one-by-one review above for that batch: with ~no data on any of them, heavy
  exploration is the point of the Thompson sampler, and an untried spot's prior already counts as
  "a spot like the others". What stays: the overseer disables a pick that turns out to be a dungeon
  mouth or monster area (names only catch obvious ones: Urukton Bluffs and Undermountain North
  Entrance passed), and the planner's own place-failure and death cooldowns keep bad picks out.
- **Tree density enters as grove capacity, not as a chopping-rate prior (user, 2026-10-03).** The
  user's point: a sparse grove dries up sooner, so each trip there pays the trip's start and end
  overhead for fewer logs. Harvest memory backs a capacity model: a tree gives ~19 logs before it's
  depleted everywhere (470 cycles, 17.7–20.2 per spot), while the share of mapped trees that give
  anything varies by spot (0.46–0.85), and trees × share × 19 matches the dry trips (Horseshoe Bay
  ~677 vs 605/613, witcher_282 ~1225 vs 1200). So `grove_logs` = trees the runner would try now ×
  yielding share (Beta per spot, pooled prior worth 10 tiles, drawn in Thompson sampling) × logs per
  tree caps Q*; when it binds the run is that one trip, and the runner is told the uncapped Q* so
  it stops on the real dry, not on the estimate. Rejected: a density term in the λ prior (density
  didn't predict logs per field hour across the measured spots; walking between trees is a small
  share of field time) and a regrowth refill within a trip (the runner lists the trees once per trip).
  Live effect: small for Witcher spots (recall overhead is 2–4 min a trip), large for small walking
  spots and for any spot whose trees are still regrowing.
- **Hatchets by expected net value, prices from observation.** Wear is one use per successful chop
  (measured, YOUNG_DEMOS); a hatchet that isn't newbied is lost on death (Terran: the corpse kept
  it). With no price, a hatchet isn't used and the plan gives the break-even price instead of
  guessing one. Prices come from what the overseer sees (`ctl lumber price`).
- **Trip value at the board price as of the trip (user, 2026-10-05).** The `prices` table is
  append-only, so price history is kept. `harness/jobs.py` values each trip's logs at the newest
  `board:<wood>` price with `t` ≤ the trip's end. A trip from before that wood's first recorded
  price uses the first price (user, 2026-10-05: not the woods.json 9.5 gp, which the commodity asks
  showed to be ~3× too low). Only a wood with no recorded price falls back to woods.json. Planning
  still uses the newest price. Rejected: revaluing every trip at today's price, which loses the
  history and makes old days look richer or poorer as prices move. `ctl lumber price --at`
  backdates an older observation (e.g. from `#sell-archive`), and `ctl lumber prices --history
  board:` lists them all. First board prices: `board:ordinary` 26.8 gp on 2026-10-05, the lowest
  commodity ask (ECONOMY §5.3), plus four colored woods from the user. Effect on the 63 trips up to
  then: value 147 773 gp (9.5 gp, 481 colored logs unpriced) → 428 442 gp, no log unpriced.
- **Regrowth from our own data.** Depleted trees came back after ~45–65 min (137 retries), not the
  20 min the runner assumed; the plan passes the fitted window (docs/NOTES.md).
- **Open:** gold/hour (ECONOMY §6 value rate; `board:<wood>` prices exist for 5 of 10 woods since 2026-10-05), routine recall home
  when a spot has a marked rune, time-of-day hazard, the Harvest Aspect tier (not observable
  without opening the `[aspect` gump). Done criterion
  (LUMBER_LOOP §9 M5): banked logs per active hour improve over the weeks on the same character
  without violating §1.
- **What it learns from is recorded per trip (2026-10-03).** Audit and gaps: LUMBER_LOOP §6 "What
  the optimizer learns from". Travel legs, the lockout, supplies, skill at the end and crowding go
  into the trip row and the `travel` events (JSON fields, no schema change); the plan treats the
  lockout and failed travel as overhead, not field time, and subtracts priced supplies per trip.
  The Jobs page shows the plan, the per-spot model, travel, tomes and supplies (VISUALIZER §2.4).
  Rejected: a separate travel table (the job events already index by job and time, and the trip
  row is what the planner reads).
- **Forest spots: a spot is the whole forest around its window (user decisions 2026-10-07).**
  Evidence (store, trips since 2026-10-04): the discovered 29×29 windows held ~500–1 000 logs
  against a hazard-optimal trip of 4 200–5 900, so 118 of 123 eligible spots were grove-bound, and
  30 of 35 stored trips ended because the window ran out of usable trees (11 dry, 19 every tree
  left within a monster's reach). Each new trip pays ~135 s (walk out 27 s, lockout 49 s, room 11 s,
  convert 33 s, store 12 s) plus the gap between runs; the plan with the grove cap lifted put the
  median spot at +17 % net logs/h. Built: `harness/lumber_forest.py`, `ctl lumber forests`
  (LUMBER_LOOP §6 "Forest spots"). The window grows like a paint bucket over walkable tiles within
  3 tiles of a tree; every spot grows at once, stops at **800 trees** (user: "your recommended
  cap", about two trips' worth), and spots whose territories meet merge while together under the
  cap. The merged spot with the most trips keeps its id; the others are disabled with
  `merged_into` and their trips count for it. The area is a set of 8×8 cells (the map's blocks).
  First build on the live store: 127 spots → 93 forests (34 merged), median 165 trees, 54 of 90
  eligible still grove-bound (was 118 of 123), median planned net 2 427 → 2 717 logs/h.
  Rejected: the whole connected forest without a cap (one forest would mix areas with different
  monster and PK traffic in one posterior, and a 30-min PK cooldown would close a whole woodland);
  4-tile links (whole woodlands percolate: witcher_56 280 → 2 056 trees); storing every tree tile
  (cells also answer "where is the area" for landings, deaths and the map, and stay small);
  forests from the land texture (tree statics are what the runner chops).
- **Library hops (user decisions 2026-10-07, built the same day): go on from the rune library
  instead of going home.** When a leg ends early (dry, every tree guarded, a monster escape, a red
  sighting: "hop elsewhere with the load") carrying less than the plan would risk, the runner
  recalls home, skips the room, and recalls to the spot a fresh plan would pick. The rule is one
  renewal-reward comparison at that spot's own rate: a leg that starts with the carried load
  (`trip_terms(load0=)`, the hop's shorter overhead) against storing first and a fresh trip
  (LUMBER_LOOP §6 "Library hops"). The library is at home, so a hop saves only the room and the
  room exit (~70 s, ~2–4 % of logs/h); with forest spots, early ends are rarer. Rejected: hop
  whenever carried < Q* (ignores that the carried load is at risk for the whole next leg, and
  Q* is a fresh trip's size, not a continuation's), and hopping to the best spot by a g of the
  greedy spot's rate (on the live store only the greedy spot ever qualified, so hops would undo
  the Thompson exploration the fresh plans do). Each leg is its own episode row (outcome
  `hopped`), so per-spot statistics stay per spot.

## Discord history capture (decided 2026-10-03)

User request: the game knowledge in the Outlands Discord (`discord.gg/outlands`) captured into a
searchable local DB that's included in the NAS backup. Code: `harness/discord_capture.py`; how to
run it: docs/NOTES.md "Discord capture".
- **A real browser, driven, with the app's own requests recorded.** Patchright (undetected
  Playwright fork) launches Microsoft Edge with a persistent profile. Edge, because Chrome isn't
  installed and installing it needs admin. The tool records the message-history responses the
  Discord web app loads while it scrolls, and never calls the API itself. Rejected:
  - a bot token: Outlands staff won't add our bot
  - a user-token scraper (DiscordChatExporter, discord.py-self): Discord flags it as a
    third-party client, and it needs a convincing client imitation
  - a TLS MITM of the desktop app: mitmproxy's own TLS/HTTP2 fingerprint replaces the client's
  - scrolling by hand: about 2000 page loads for 100k messages
- **A new, throwaway Discord account, no VPN (user decisions).** The laptop isn't on the user's
  home connection, so an IP restriction would be tolerable. A VPN would also break the game
  connection (VPN exit IPs are SYN-dropped on 2593, docs/NOTES.md "Network observations").
  The account only reads: the tool has no code path that posts.
- **Depth: the game-info channels, as far back as practical**, not every channel to its start.
  The crawl takes a `--until` date per run and resumes from the oldest stored message.
- **Separate DB (`harness/data/discord.db`), not the memory store.** It's a different domain,
  with its own writer and its own lifecycle, and capture volume shouldn't bloat the hourly
  `harness.db` snapshots. Keyword search is FTS5 with porter stemming, as in `knowledge`; semantic
  search is below.
- **Backed up, not committed.** Captured messages are runtime data (AGENTS.md Rule 0), so
  `backup.py` snapshots `discord.db` like `harness.db` (own `discord/` folder, same retention).
  The browser profile holds the login session, so it's treated as a credential: gitignored and
  not backed up.
- **Images are captured too (user request).** Image attachments are downloaded to
  `harness/data/discord_media/` and backed up with the DB. They're fetched from the CDN by URL,
  not taken from the page's own image loads. The page only loads resized previews of images that
  scroll into view, and a fast crawl skips many of them. The signed URLs expire after ~24 h, so
  the downloader runs inside serve, right after capture.
- **Semantic search (user request 2026-10-03): hybrid, local, a sidecar index.**
  `harness/discord_search.py`. It merges, by reciprocal rank fusion:
  - meaning: embeddings of conversation chunks
  - words: FTS5 BM25 over single messages

  Single chat lines are too short to embed well, so a chunk is one exchange: consecutive
  messages less than 10 min apart, at most 12 messages or 1200 characters. The model is
  `BAAI/bge-small-en-v1.5` via fastembed (ONNX on the GPU via onnxruntime-gpu, with a CPU
  fallback; nothing leaves the machine after the model download). Vectors live in
  `harness/data/discord_vec.db`, keyed by the chunk text's hash,
  and search is brute-force numpy cosine. Rejected:
  - sqlite-vec: an extension for a corpus that numpy scans in milliseconds
  - torch/sentence-transformers: a 2 GB dependency, as in `.venv-laya`
  - a hosted embedding API: sends the corpus out and adds a per-query network call

  The vector file can be regenerated from `discord.db`, so it isn't backed up.
- **Not wired to the overseer (user decision).** The search is a tool for building knowledge, not
  something the playing agent asks directly. Discord chat is unreliable: jokes, outdated patch
  info, wrong answers. The vetted layer on top is "Discord knowledge base" below: only its
  official/consensus output reaches the agent, as `knowledge` entries.

## Discord knowledge base (decided and built 2026-10-03)

User request: turn the captured Discord history into a consolidated set of likely-true game facts
with sources and confidence, for the overseer and for coding agents. Code: `harness/discord_kb.py`
(state in `harness/data/discord_kb.db`, gitignored, backed up); operation: docs/NOTES.md
"Discord knowledge base". User decisions: Sonnet for both LLM stages via headless `omp`; vetted
facts go into the existing `ctl know` table; a generated digest `docs/research/DISCORD_KB.md` is
committed; prices (#buy/#sell) deferred; #patch-notes and #announcements crawled and treated as
authoritative.

- **Two LLM stages, deterministic checks around both.**
  - Extraction reads one window (a UTC day of one channel, whole conversation chunks up to 24k
    characters) and returns atomic claims. Each claim must cite message ids from its window and
    a verbatim quote that is a substring of a cited message; anything else is dropped. Authors
    and timestamps come from discord.db, never from the model.
  - Adjudication sees a cluster of similar claims (author-labelled A1..An, official flag, stance,
    hedging) and proposes one fact with a verdict. Code then recounts independent supporting and
    contradicting authors and caps the verdict: `official` needs an official claim; `consensus`
    needs >= 2 supporting authors outnumbering the dissent, else `single_source`/`disputed`.
    Confidence is a fixed function of the final verdict (official 0.85, consensus 0.65–0.80,
    single_source 0.5, disputed 0.35), not the model's opinion.
- **Clustering:** leader clustering over bge-small embeddings (the search model, on the GPU) at
  cosine 0.86, same `kind` only. Deterministic and stable: old claims keep their cluster, a
  cluster is re-adjudicated only when its member set (signature) changes. Rejected: HDBSCAN /
  agglomerative (reshuffles every cluster on each run, so every fact would churn); letting the LLM
  group claims (costly, unstable).
- **Incremental by construction:** window ids hash channel, day, part, first/last message id and
  the prompt version, so backfilled history only re-extracts the days it touches, a prompt
  change re-extracts everything, and a vanished window's claims are deleted (their clusters
  dissolve or re-adjudicate). Only days before today (UTC) are processed: a day still filling up
  would be re-extracted on every run.
- **Promotion rules (the overseer's own observations win):** only official/consensus facts, source
  `community` (0.6 default, new in `knowledge.SOURCES`) or `doc` for official ones, importance
  capped at 6 so a mined fact never becomes a `brief` standing item. Re-runs are no-ops (hash of
  what was promoted). An entry the overseer retracted or superseded is never re-added, also not
  under new wording from a re-cluster (content-hash check). When a fact's verdict drops, the
  pipeline retracts its entry only if it added it and nobody confirmed it since.
  Promotion state is kept per target DB (`promotions` table, not columns on `facts` as first
  planned): the trial promote into a copy of harness.db otherwise left the copy's entry ids on
  the facts, and the real promote then took them for ids in the real store.
- **LLM call shape:** `omp -p --no-tools --no-session --no-extensions --no-skills --no-rules
  --mode json` from the temp dir (no repo context loads, ~330 tokens of overhead), prompt in an
  attached temp file. Failures: infra retry once after 30 s, invalid JSON/validation retry once
  with the error appended, output truncation splits the window (or the cluster batch). Every
  attempt is logged in `llm_calls` with its cost; `--max-cost` (default $100 since 2026-10-04, was
  $40) stops a run from submitting more calls (exit 2, rerun resumes). The calls go through omp's
  OAuth login (the user's Claude subscription), so the cost is a list-price estimate of usage, not
  a bill; the user set the cap from that.
- **Not done:** prices (deferred by the user), images in messages (not read; image-only messages
  are skipped as in search).

## Semantic `ctl know search` (decided and built 2026-10-03)

User requests: make `ctl know search` a semantic search (the knowledge table now also holds the
Discord facts); GPU over CPU as the default, always. This reverses the earlier "no embeddings"
call (docs/MEMORY.md): word search missed paraphrases ("my character died" vs "resurrection").

- **Hybrid, not pure vectors:** the 60 nearest by cosine and the 60 best BM25 hits, fused by
  reciprocal rank, then the existing recency/importance/confidence/nearness score. Exact item
  and NPC names still win through the word ranking; pure cosine ranks them below vague matches.
  Same model and fusion as `discord_search.py`, through one shared loader, `harness/embedder.py`.
- **Vectors in harness.db (`knowledge_vec`), embedded lazily by the searcher.** Writers (proxy,
  `ctl know add`, `discord_kb promote`) never load the model; a search embeds whatever changed
  first (~1 s on the GPU for the whole ~1k-entry store). Rejected: embedding at write time (every
  writer would need the GPU stack and the 1 s load); a sidecar vector DB (another file to back up
  and keep in sync for ~1k rows that numpy scans in microseconds).
- **ctl's Python got the GPU stack** (fastembed-gpu + CUDA 13 wheels in the system Python 3.13,
  same versions as `.venv-discord`). Rejected: running ctl from the venv (every other ctl path is
  tested on the system Python) and shelling out to the venv per search (an extra process start).
  Cost: ~1.5 s per `know search`/`brief` for the model load; a resident embedding service would
  remove it but isn't worth a daemon yet.
- Without fastembed in the running Python, recall falls back to words and says so (`recall:
  words`), so a different `UO_PY` degrades instead of failing.

## Reaching the whole map: Witcher-rune spots and guarded walks (decided and built 2026-10-03)

User request: can the overseer and the loop explore the whole overworld over time? The answer was
no: discovery searched only 30–110 tiles from banks, and spots that failed never looked bad. After
the research (docs/research/WORLD_LOCATIONS.md) the user approved spots reached by recall, using
the public Witcher rune library in Cambria heavily for now. Their changes: commit the Witcher table;
every lumber character has at least 60 Camping and 60 Magery. The user also asked for safety rules
on the overseer's walks after a harpy nest killed Hackworth on a blind `ctl act goto`.
- **A spot says how to get there and back** (`access`, `home`), and the runner does the travel
  itself: walk to the tome, recall out, then recall home on the PK-escape book's default rune.
  Rejected: the overseer travelling before every trip. That costs an LLM turn per trip, and walking
  home from a far rune isn't possible (planner range, wilderness risk).
- **Witcher runes are the candidate places.** They cover the map densely, each already has a public
  rune, and their names flag monster camps. Atlas POIs need hiking (campfire flow, an unlock visit
  per POI); deferred.
- **Failed trips are evidence.** A trip that got nothing for a reason that belongs to the place
  counts as field time with 0 logs, and two in a row set the spot aside for a week. Without this,
  an unreachable or in-town spot would keep its optimistic prior and keep being explored.
- **Travel is learned** from the gaps between trips at different spots; at the library hub, rune
  spots cost no travel.
- **Overseer walks are guarded by default** (`travel_guard.py`, `--no-guard` to walk into a fight
  on purpose). Hostile creatures become zones the route bends around. A goal in a creature's reach,
  a hostile player, or low hits under attack stops the walk. Sightings are remembered, so bodies
  count as aggressive and areas are avoided on later walks. Rejected: aborting on any creature in
  view (every wilderness walk would stop) and walking on while hit (what killed us).

## Hunt crawl (decided and built 2026-10-03)

User request: the hunt runner should crawl a dungeon instead of standing on one spot: roam each
floor, fight what it meets, and go only as deep (each floor more dangerous) as it can fight
efficiently. Plan, data and limits: docs/HUNT_LOOP.md "Crawl"; code: `harness/crawl.py`,
`loop_hunt.py --crawl`.
- **The NPD is one floor (dungeon level 1)**, by the data: the map BFS from the arrival reaches
  10,829 tiles in one storey, and neither the store's teleporters nor the client's Atlas packs know
  a way to another part. So "deeper" is route distance from the one exit: depth bands of 40 steps,
  called **zones** and numbered from 1 (zones 1–5, the farthest tile 182 steps out), each opened
  only when the one before is known and the next one's predicted hits lost per minute is
  acceptable, closed again when it proves worse. Floors / dungeon levels keep the game's numbering
  from 1 (user, 2026-10-03: levels start at 1 and go up the deeper you go); zones are internal, so
  they don't borrow the word "level". The first version called the bands "levels 0–4"; stored rows
  from it (`levels`, 0-based) are read as zones from 1.
- **Patrol over coverage waypoints, chosen by staleness × value × crowd × zone rate / distance**,
  walking with the Mover and stopping as soon as something is in reach. Rooms are covered by
  route, not straight line, so a waypoint never "covers" the room behind a wall.
- **A shrunk per-creature and per-zone model, persisted in the store** (`fight` job events, the
  visit rows' `crawl` block): the store's 178 earlier kills (rebuilt from the event log) are the
  prior, so the first crawl already knows mongbats, and a creature fled from stays avoided across
  runs. Thresholds are task arguments.
- **Safety reuses what exists**: the leave rules, the route margin per step (now from wherever we
  stand), the Mover's danger zones (avoided creatures) and teleporter avoidance; a survival leave
  runs the shortest route to the exit.
- Rejected:
  - **A fixed patrol loop** (a tour over all waypoints, e.g. nearest-neighbour + 2-opt): it can't
    stay in a productive area, skip a crowded or depleted one, or bend around a creature it
    learnt to avoid, and the same loop every time is a bot pattern (ANTICHEAT.md).
  - **A multi-armed bandit over fight spots** (Thompson sampling over a handful of `--fight-spot`s,
    as `lumber_opt` does over lumber spots): it would still stand still between moves (the
    Stationary Penalty, idle time while bats come), it covers only the spots someone picked, and it
    learns nothing per creature, so it can't tell a too-strong creature from a dry spot.
  - **Room segmentation (watershed) for areas**: the coverage cells already are room-sized (one
    per room centre, a chain along corridors) and need no tuning.

## PK survival: stealthers, Magic Reflection, mounted flight (user notes 2026-10-03, open)

After the second Bastet death (docs/NOTES.md "Second Bastet death"): he appeared at 11 tiles
carrying the Stationary Penalty, never showed on Tracking, and his first spell landed 1.9 s after
he appeared, before the 2.0 s recall could finish. The user's reading: he was hidden and
stealthing near us and popped out to kill. Our Tracking (72.8) sees hidden players only within
~8 tiles, 10 % of its normal range (knowledge #953). Spell interruption and escape timing:
docs/research/SPELL_INTERRUPTS.md (the escape now recasts until it lands, waiting out the measured
disturb recovery; a reflected opener would have saved Nusero; flight and healing would not). To do:

- **Detect stealthers.**
  - Investigate the skill templates harvesters use against PKs (memory has a dungeon lockpicker
    build with 100–120 Detect Hidden and Tracking, #389; the Discord knowledge base and wiki for
    more). The numbers that matter: hidden targets show up on Tracking within (10 + 40 ×
    DetectHidden/100) % of the Tracking range: 30 tiles at 100 Tracking + 50 DH, 50 at 100/100.
    Detect Hidden itself reveals within 8 × skill/100 tiles with skill % success (#954).
  - Try running Detect Hidden periodically during the lumber loop (on self, between chops) once a
    character has the skill; Hackworth has none. Measure what it finds and its skill-use cost
    against the chop rhythm. Reveal (6th circle, 60/80 Magery) only covers 3 × Magery/100 tiles
    around its target point, so it isn't an early warning (#955).
- **Mounted flight with scripted healing.** On horseback and healing on the move (potions, Greater
  Heal), we could probably have run from this PK and others instead of standing in a recall that
  gets interrupted. Hackworth's horse died on 10-03; mounted pace is 0.1 s per step (PLAN "Player
  houses and mounted pace"), the same as a mounted PK, so flight has to use terrain, line of sight
  and the PK's hamstring timing (Red sighting item 4). The Prevalia Stables bonded-horse quest
  (TRAVEL_DEATH §1.4) gives a horse that survives death. Measured (SPELL_INTERRUPTS §3.1): at
  Nusero we were on foot against a mounted PK; at Terran a recall at sight beats his first hit by
  ~2 s, and his melee (~15 HP/s) outpaces any heal we have (Greater Heal 24–30, 1.25 s, broken by
  the same hits). Flight only pays with a horse and a guard zone or line-of-sight break in reach.
- **Magic Reflection up while lumbering.** Reflects the next hostile spell cast on us, so a PK's
  opening spell (the one that broke the recall) bounces: at Nusero it was Weaken, and recall 1 would
  then have landed 0.49 s before his Harm. It's 5th circle: min 50, 100 % at **70** Magery, so at
  Hackworth's 60 about 50 % (from a scroll +20 → 100 %); reagents garlic, mandrake root, spider's
  silk (mages sell all eight reagents but can sell out until their stock refreshes; Garritt was out
  of silk, ash and nightshade on 10-03), scroll 300 gp at Garritt. In PvP it reflects at most 2
  spells and stays after the first only with a 35 % × Inscription/100 chance (#215, wiki Magery).
  Open: how long it lasts on Outlands,
  how to see it's up (buff icon), and recasting it at the start of each trip and after each reflect.

## Smart Harvest for lumber: built offline 2026-10-04, first live trip 2026-10-04 22:06

Live 2026-10-04 22:06–22:17 (Outland Dan, Horseshoe Bay, the room-storage trip in docs/NOTES.md
"First live room-storage trip"): 527 s of Smart Harvest chopping over 11 stands, each ended by
"nothing nearby has wood" and the next stand; 240 logs. The reach measurement isn't analysed yet.

**Decision (user, 2026-10-04): chop at a Razor script's pace.** Every harvester on the shard runs a
script that uses the hatchet ~0.2 s after each reply (ANTICHEAT.md §3, §8.14), so the human pauses
in the chop cycle (use 0.9 s, aim 0.95 s, 2.2 s between chops, fidgets, hesitation) only cost
logs: the cycle was 9.4 s median against the server's 4.2 s. Now `humanize.SCRIPT_MEDIAN` (0.2 s,
0.1 s, σ 0.2, no fatigue) paces it, and the trapped-pouch stash runs during the server's 4.2 s.
Walking, menus, the room and conversion keep their human texture.

**Decision (user, 2026-10-04): the lumber runner switches from targeting a tree to Smart Harvest,
and that is the next lumber work.** Another agent implements it. Smart Harvest: double-click the
hatchet and answer its cursor with yourself; the server chops a nearby tree that still has wood
([wiki](https://wiki.uooutlands.com/Smart_Harvest): "target yourself/status bar … automatically
harvest from any nearby spot that has resources remaining").

**Evidence that it works:**
- The user has used it in our sessions.
- Capture `20261001_214649` (Hackworth, human-driven, `python harness/loop_mine.py timeline
  20261001_214649`, 23:18–23:52):
  - At 23:20 (1918,2612): hatchet `0x4B6BB8A7` double-click → cliloc 1010018 "What do you want
    to use this item on?" + cursor → client `0x6C` type 0 on our own serial (x/y of our tile,
    graphic `0x0190` = our body; the stock client fills these in) → captcha gump → after the solve
    "You do not see any harvestable resources nearby." and, overhead from self, "You cannot produce
    any wood from that."
  - At 23:47 (1905,2616), the same sequence next to trees → cliloc 500495 "You hack at the tree
    for a while, but fail to produce any useable wood.": an ordinary chop result.
- Every public lumber script does it this way (Jaseowns' three, ANTICHEAT.md §3; Discord KB
  "smart harvesting", 3 authors).

**Why:**
- One stock action per attempt with no tree to choose and no aim at a tile.
- Fewer walks: the runner moves only when nothing near it has wood, closer to how scripted
  harvesters play (Razor can't walk on Outlands, ANTICHEAT.md §3).
- Today's per-tree targeting (`loop_lumber.attempt` → `actions.target_xyz` on the tree's static)
  stays something a player can do, so this is a simplification, not a detection fix.

**What the implementer has to settle** (unknowns are measured live, not guessed):
- **Answering the cursor:** with our own serial, byte-equal to the client's packet above. Reuse what
  `ctl act target self` sends rather than writing a second builder.
- **Outcomes:** the existing clilocs (logs, 500495 fail, 500493 not enough wood) still come per
  attempt. New: "You do not see any harvestable resources nearby." means nothing in range has wood,
  so move to the next stand. Map it in `outcome()` next to the old "not a tree" / depleted cases.
- **Range and choice:** how far Smart Harvest reaches, and which tree it picks, are unknown. Measure
  them on the first attended run: stand positions, the trees around them, the result, and logs per
  stand. The tree list (`candidate_trees`, map `find_trees`, harvest memory) then chooses *where to
  stand* (most trees with wood within the measured range), not what to target.
- **Harvest memory:** results are kept per tree (`memory.harvest_record(node, …)`). With Smart
  Harvest the server doesn't say which tree it chopped. Record per stand tile, and on "no harvestable
  resources nearby" mark the trees within range as depleted for the regrow window. Keep the episode
  and trip stats `lumber_opt` reads (attempts, successes, logs, chop and walk times) unchanged in
  meaning.
- **Stationary Penalty:** standing longer per spot brings it on sooner (301–315 s without a step);
  `unstick` already handles it before each chop.
- **Captchas:** unchanged. The self-target attempt resumes after the solve like a tree attempt
  (NOTES "The harvest attempt that raised the captcha resumes after the solve").
- **Cutover:** replace per-tree targeting outright (no flag, no fallback path) unless the live
  measurement shows Smart Harvest failing somewhere the old way works; then record the case here.

**Done when:** offline tests (`test_loop_lumber.py`) cover the new cursor answer and the "no
harvestable resources nearby" move-on; an attended live trip chops a quota with Smart Harvest
only; trip and episode rows still feed `lumber plan`; the measured range and the tree it picks
are written to docs/NOTES.md and LUMBER_LOOP.md §2.

**Built offline (2026-10-04; live trip pending):** `harness/loop_lumber.py`, cut over with no flag.
- **Cursor answer:** `self_target(st, cur)` = `combat.target_self` (the fields `ctl act target self`
  sends): our serial, movement's x/y/z, our body. `test_loop_lumber.py unit_capture_smart_harvest`
  replays capture `20261001_214649` through the proxy's SessionTap and gets the client's exact bytes
  at 23:20 and 23:47 (`6c00 <cursor> 00 0020f127 <x> <y> 00000000 00000190`). The z must be
  movement's: the world model's self z said 10 there while the client sent 0.
- **Outcome:** `nothing_near` = either line in lumber.json `harvest.nothing_near_texts`; the stay at
  the stand ends, the trees within `SMART_RANGE` of it are marked `nothing_near` in harvest memory
  (out of wood for `--regrow-min`) and leave the trip's list. 500493 also ends the stay, without
  marks (which tree it was is unknown). The not-a-tree outcome is gone: nothing targets a tree.
- **Stands:** `next_stand` picks, among the 6 nearest trees, the planned walk with the least cost
  per candidate tree within `SMART_RANGE` of the stand it ends on; `work_stand` stays up to
  `--max-attempts-per-stand` (60, was `--max-attempts-per-tree` 25). `unstick` still runs before
  every chop, so long stays reposition or clear the Stationary Penalty as before; captchas are
  unchanged (the attempt resumes after the solve, as at 23:20 in the capture).
- **Reach:** `SMART_RANGE = 1`, the only value the evidence proves (23:47: one tree within 6 tiles,
  at distance 1, and the server turned us to face it, 0x77 dir SE). RunUO's by-hand reach is 2.
- **Memory:** attempts are recorded on the stand tile (`harvest_record(facet, x, y, z, None, …)`);
  trip and episode rows are unchanged (attempts, successes, logs, `chop_s`, `walk_out_s`,
  `tree_walk_s`, now the walks between stands). `lumber_opt.tree_yield` counts a `nothing_near`
  mark as "out" but not "tried"; `regrowth` makes no pairs from marks (a mark only ever says "not
  regrown", the server never says which tree regrew), so the regrowth fit rests on the per-tree
  data from before the cutover. Open: a way to learn regrowth from stands.
- **Measurement:** each stand is a `stand` job event (`trip`, `spot`, `stand` [x, y, z], `anchor`,
  `range`, `trees` [dx, dy, distance, graphic] within 6 tiles, `attempts`, `successes`, `logs`,
  `faced` (world.self.direction after each chop: RunUO turns the harvester toward the tree), `end`
  (nothing_near / depleted / quota / break / max_attempts / interrupted: …), `s`). The run log
  prints the trees in the faced direction per chop.

**Attended live trip (the user runs it, at the client):** `python harness/ctl.py lumber plan`, then
its `command` with `--trips 1`, i.e. `python harness/ctl.py run lumber --spot <id> --trips 1
--logs-per-trip <n> --regrow-min <m> --timeout <s>` (direct: `python harness/loop_lumber.py --spot
<id> --trips 1 …`). Watch: every chop cursor answered with
ourselves (no tree aimed at), "You do not see any harvestable resources nearby." moving the runner
to the next stand, captchas resuming the attempt, repositions on long stays. Measure afterwards
from the `stand` events (`sqlite3 harness/data/harness.db "SELECT data FROM job_events WHERE
kind='stand' ORDER BY t"`): the reach = the largest distance of a tree the server faced on a chop,
and whether a 'nothing nearby' came with candidate trees 2+ tiles away that later gave wood; which
tree it picks = the faced trees per chop (nearest first? the same tree until dry?). Then set
`SMART_RANGE` and write the numbers into docs/NOTES.md and LUMBER_LOOP.md §2.

## Keep thieves off the logs: trapped pouch + keep-away (built offline 2026-10-04, pouch flow live 2026-10-04)

User request (2026-10-04): add protecting our logs from thieves to the plan, after seeing the
community's trapped-pouch scripts. Built offline 2026-10-04 (below, "Built offline"). Live
2026-10-04 22:06–22:17 (docs/NOTES.md "First live room-storage trip"): the logs went into the
trapped pouch, our own set-off in the room cost 1 hit and was not taken for an attack, the spent
pouch went into the chest. No thief came, so the keep-away and the pop alarm are still unproven live.

**Where we stand:**
- `harness/ledger.py` only notices a theft afterwards: a `theft` job event plus a `theft_suspected`
  juncture, and the loop carries on. `lumber_opt` then prices theft risk into the trip size.
- Nothing prevents a theft. THREATS.md §7 T3 (keep players ≥ 2 tiles away) and the 400-board carry
  cap were never built. `threats.py` only marks a blue player nearby as `watch`.
- Seen so far: one theft (10 mandrake root, 2026-10-03, "Caputo Wood" walked up to 1 tile; docs/NOTES.md
  "A pickpocket, not an attack"). The memory store has 0 `theft` events and 0 `theft_suspected` junctures
  as of 2026-10-04.

**What thieves can do** (THREATS.md §4, wiki, H):
- About 200 logs per successful steal at 100 Stealing, one steal per 5 s.
- The thief must stand within 1 tile.
- Snooping a non-empty trapped pouch makes it explode; snooping an empty one is blocked.
- **They look friendly until they steal** (user, 2026-10-04). A Red Hand thief is a blue, and only
  turns grey to us with the steal itself: Caputo Wood went notoriety 1 → 3 at the moment our reagents
  left the pack (NOTES "A pickpocket, not an attack"). So notoriety gives no warning, and **any player
  within 2 tiles of us while we harvest is almost certainly a thief.** Nobody else has a reason to
  stand on top of a lumberjack.

**What players say** (Discord capture, M):
- A container with items in it can't be stolen whole (Kaitlyn 2026-07-01, Halic 2026-07-02).
- Logs dropped into a trapped pouch are hidden until a thief snoops it, and the explosion is the
  warning to leave. Nested pouches buy more time (jeem 2026-08-28).
- Hiding a bag behind other items doesn't work.
- Against it: one player lost supplies from a trapped pouch that never exploded (Kataleon
  2026-07-17; nobody could explain it), and a former thief says "if a thief wants something they can
  most likely get it". **So a pouch is a delay plus an alarm, not a lock.**
- The same idea in public scripts: Jaseowns' "Snippet for moving logs to trapped pouch" and the miner's
  `hideIngotsInRedPouch` (ANTICHEAT.md §3).

**Plan:**
1. **Logs into a trapped pouch.** After each successful chop (or every few), move the top-level log
   stack into a trapped pouch in the backpack with the stock lift/drop and human pacing, as the
   snippet does. Boards from converting go there too.
   - The ledger has to treat the move as expected, not as a loss. It must still see the pouch's
     contents as carried, and still see a theft out of the pouch.
   - The trip's log counts (`attempt`, quota, `lumber_opt` stats) must count logs inside the pouch.
2. **The explosion is the alarm.** A popped pouch near any player means a thief is at work.
   Raise the existing urgent threat path: recall, and keep away from the spot for `THIEF_COOLDOWN`
   (THREATS T4).
   - **Measured 2026-10-04 (owner's pop, docs/NOTES.md "A trapped pouch popped by its owner"):** the
     pouch in our pack is re-sent with hue 38 → 0, with an explosion effect `0x36BD`, sound `0x0307`,
     −1 hit and "You now have N trapped pouches remaining."
   - **The trigger:** the explosion on us (effect `0x36BD` at our tile, sound `0x0307`), or a hue-38
     pouch in our pack turning hue 0, when we didn't just double-click that pouch. The user knows
     the explosion shows on us when a thief pops our pouch (2026-10-04), so either signal works. Use
     both, whichever arrives first. Still unmeasured: whether a thief's pop also costs us a hit
     point.
   - Replace a popped pouch (now hue 0) before the next trip, and move its logs into a live one.
3. **Keep-away (THREATS T3): any player within 2 tiles is a thief.** It doesn't matter whether they
   look friendly, and they don't have to stay. While harvesting, a player (not an NPC, pet or follower) at
   ≤ 2 tiles is treated as a thief at once:
   - Step out of reach to ≥ 4 tiles after only the humanised reaction delay. One step beats the 5 s
     steal cooldown, where a 2 s recall would still leave them in reach.
   - Post an attention juncture and log the player as a suspected thief.
   - If they close to ≤ 2 tiles again: recall, and keep off the spot for `THIEF_COOLDOWN` (THREATS T4).
   - `threats.py` changes from `watch` to this response for players in steal range during a harvest job.
   - Friends or guildmates, if we ever have any, would need an explicit allowlist; there is none today.
4. **Getting pouches:** buy them from a provisioner. Errol sells "Trapped Pouch" at 25 gp
   (measured 2026-10-04); they arrive as hue-38 pouches. The trip plan carries two or three, like
   hatchets and reagents, and tops up at the provisioner. Casting Magic Trap ourselves isn't needed
   at this price.

**Rejected:**
- **Attacking the thief:** Heat of Battle blocks recall, and it's PvP (THREATS §4.3).
- **A locked box in the pack:** we can't drop into a locked container in our own backpack, and thieves
  can lockpick it.
- **Holding the bag on the cursor** (a Discord trick): no stock client does that while harvesting, and
  the cursor is busy with the hatchet.
- **Hiding the bag behind other items:** players say thieves move items aside.

**Open (user):** popping a pouch to break a paralyze (ROADMAP Q8) stays a separate decision. This
plan only carries the logs in one.

**Done when:**
- Offline tests cover the pouch move in the ledger (no false theft; a real loss from the pouch is still
  a theft) and the keep-away trigger.
- An attended live trip keeps its logs in the pouch end to end, including the convert and the bank
  deposit.
- Replay of `20261004_113229` raises no alarm for our own two pops. The same hue change without our
  double-click (a synthetic case built from it) raises the thief alarm.

**Built offline (2026-10-04; live trip pending).** Code: `harness/pouch.py` (new), `loop_lumber.py`,
`ledger.py`, `threats.py`, `world/runtime.py` + `layouts.py` (0x54), `nav.beyond`, `lumber_opt.py`,
`ctl.py` (buy).
- **One pouch a trip, converted at the bank (deviation from item 1).** A live trapped pouch can't be
  opened without setting it off, and the logs inside can't be targeted while it is closed. So the
  logs stay in the pouch from the chop to the banker; there the runner sets its own pouch off
  (`unpack`: 1 hit, no alarm), opens it, converts inside it and banks the boards and the spent
  pouch. Trip order is now harvest → to_bank (walk + "bank") → convert → store. Boards are never
  carried in the field, so no second pouch is needed. LUMBER_LOOP §6 already said converting in the
  field gains nothing (same weight, same loss). Boards land in the logs' container [INFERENCE: RunUO
  ScissorHelper; check live].
- **Stash:** after each successful chop, `stash` drags the loose log stacks onto the pouch's icon
  with the stock lift/drop (DROP_AUTO, like the deposit), `use`/`drag` pacing. The ledger has an
  expectation `("moving", serial, pouch)` for it: the stack's vanish on the lift (the server deletes
  it, 20261004_113229 2:50) or a merge is expected; it resolves once the stack shows in the pouch, so
  a later grab of it is theft again. `count`/`in_pack` see the pack at any depth (attempt tallies,
  quota, `carried`). A salvage abort stashes instead of converting.
- **Pop alarm:** `pouch.PopWatch` per state read (`check_pouches`): a location explosion `0x36BD`
  within 2 tiles, sound `0x0307` within 2 tiles (both new world events), or a live pouch turning hue
  0. It is ours when a C2S double-click on that live pouch preceded it (hue), or one came within
  `OWN_POP_S` 3 s before (explosion/sound). Our own hit is acknowledged (`Watch.acknowledge(hits=)`,
  `start_hits`). Anything else is `pouch_alarm` → `thief_out`: recall when afield with a book
  (pk_escape + threat junctures), else stop; a `thief` job event (trigger `pouch_pop`). RunUO's
  MagicTrap puts the five explosions on x±1, y±1 and (x+1,y+1,z+11), none on the tile itself, and
  damages whoever opened it [INFERENCE: so a thief's pop costs the thief the hit, not us].
- **Keep-away:** `threats.Params.steal_guard` (0 = off; the runner sets 2) gives any non-hostile
  player within 2 tiles action `thief`. While chopping at a stand (not while walking between
  stands: we pass players, a thief comes to us), `thief_near` raises `KeepAway`: a `read` reaction
  pause, then a walk to a tile ≥ 4 tiles from him (`nav.beyond`); he goes into the trip's danger
  zones; attention `thief_near` juncture and a `thief` job event (action `keep_away`). Still in range
  after the walk, or closing in again at a later stand: `thief_out` (trigger `closed_again`).
- **Cooldown:** `lumber_opt.THIEF_COOLDOWN_S` = 20 min after a `thief` event whose action isn't
  `keep_away` (`eligibility`).
- **Supply:** `pouch_ready` before every trip: no live pouch → attention `low_supplies`
  (`item: "trapped pouch"`) and the run stops before the trip. `ctl lumber plan` returns
  `pouches {carry 3, per_trip 1, live, spent, buy}`; the trip row's `supplies.trapped_pouches` is
  priced at `trapped_pouch` (record it: `ctl lumber price trapped_pouch 25 --source "Errol 2026-10-04"`).
  Restocking is the overseer's: `ctl act buy <provisioner serial> trapped pouch --amount N`. `ctl act
  buy` no longer refuses when the pack lacks the gold: the vendor takes it from the bank account
  (live 1:25, Hackworth had 0 gp in the pack) and ctl books the amount from that line. Gap: the
  runner doesn't walk to a provisioner itself (no provisioner positions in the spot data).
- **Tests:** `harness/test_pouch.py` (replay of `20261004_113229`: both own pops raise no alarm;
  without the two double-clicks every signal is the alarm), `test_ledger.py test_trapped_pouch`,
  `test_threats.py test_steal_guard`, `test_world_units.py test_pouch_pop_near_self`,
  `test_lumber_opt.py` (cooldown, pouch plan), `test_ctl.py` (bank-paid buy), `test_loop_lumber.py`
  (main run: stash, own pops at the bank, a grab from the pouch booked as theft; `thief_keep_away`,
  `pouch_pop`, `no_pouch`).

**Attended live trip (the user runs it, at the client):** carry 2–3 hue-38 pouches (Errol: `ctl act
buy <serial> trapped pouch --amount 3`), then `python harness/ctl.py lumber plan` and its command with
`--trips 1` (`python harness/ctl.py run lumber --spot <id> --trips 1 --logs-per-trip <n> --regrow-min
<m> --timeout <s>`). Watch: each chop's logs dragged into the pouch (log "stashed N"); no
theft_suspected for the drags; at the bank "set off our trapped pouch", one "-1", no threat juncture,
the pouch opens on the next double-click, the logs convert inside it (where the boards land), boards
and the spent pouch go into the bank box. Not to stage: a thief.

## Staff alarm on an invulnerable player in view (built offline 2026-10-04, after the thief guard; no staff seen live yet)

User idea (2026-10-04): a GM is probably an invulnerable character, so raise the staff alarm when
one comes on screen while we chop, without waiting for them to speak. The worry was false alarms
from vendors. Measured over all captures (throwaway world-model replay of every `logs/session_*`,
2026-10-04):
- **"Invulnerable on screen" alone would fire on most trips.** Notoriety 7 (yellow) was on screen
  while we stood at the tree being chopped (within 3 tiles of a harvest attempt, ±15 s) in 12 of 13
  lumber sessions, within 8 tiles in 10. 317 mobiles. Away from Shelter they were mostly player vendors
  ("TOP TIER VENDOR", "SHOGUN SHOP", "Scrolls - Junk 7k ea", "Goods for the boyz") and town NPCs.
- **Flag `0x20` (`threats.FLAG_PLAYER_HINT`) separates them.**
  - On 1,474 human mobiles at notoriety 1/3/4/6 (players, including the reds), and on our own character.
  - Never on any of the 2,607 notoriety-7 sightings (736 distinct NPCs, vendors, player vendors, criers).
  - Never on about 2,400 non-human mobiles.
  - Human NPCs (guards like "a prevalian footman", named NPCs) don't carry it either.
  - **Notoriety 7 together with `0x20` has never been seen.**
- [INFERENCE] A GM is a player account, so it should carry `0x20`, and the user's guess puts it at
  notoriety 7. Neither is confirmed: no staff have been captured. A GM shown blue or grey, or hidden,
  slips past this rule; the speech hold and the other staff hints still apply.

**Plan:**
- **New staff hint "invulnerable player":** a mobile in view with notoriety 7 and flag `0x20`. It
  joins `speech_guard.STAFF_HINTS` (GM body, staff-like name, not on screen, attendance check), but
  fires on sight, not only when they speak.
- **During a harvest job it raises `gm_suspected` and the repeating staff alarm** (`alerts.post_gm`)
  through the path loop_lumber already uses for hinted speakers. It holds the job like a speech hold
  until the human or overseer acks. Wire it into the hunt runner the same way where it shares the
  plumbing.
- **Log every sighting** of the combination, job or not (serial, name, body, position, hue, flags,
  equipment), as a `staff_sighting` job event. That record is how we learn what staff look like on
  Outlands.
- **Vendors and NPCs (notoriety 7 without `0x20`) stay ignored.** No movement or arrival heuristics:
  the flag rule needs none on the data we have.

**Done when:** offline tests show the hint on a synthetic notoriety-7 + `0x20` mobile (alarm raised,
job held). A replay of every capture produces **zero** alarms from this hint (the measurement above,
as a test over the committed captures). THREATS.md §1 and OVERSEER.md describe the hint.

**Built (2026-10-04, offline; done-when met):**
- `speech_guard.py`: `invulnerable_player(mob)` (notoriety 7 and flag `0x20`), the hint
  `"invulnerable player (notoriety 7 + player flag 0x20)"` in `STAFF_HINTS` (so `staff_hints`, the
  `gm_suspected` summary and a speaking invulnerable player's `evidence` carry it), and
  `SpeechGuard.sightings(world)`. It reports each such mobile coming into view as a speaker-shaped
  entry (`type: "sighting"`, `text: None`, body, hue, flags, notoriety, position, `worn` layers from
  the world model). One that stays in view is reported once; one cleared by an all-clear stays out
  for the usual 15 min; `first` marks its first sighting this run. Our own mobile never counts.
- Why reuse the speech hold instead of a threat class: the hold, its ack, the repeating alarm and
  the "no resume while `gm_suspected` is open" rule were already there and tested. threats.py
  still calls notoriety 7 an NPC, so a GM next to us is never a `thief` (no keep-away) and the
  thief guard is unchanged.
- `loop_lumber`: `staff_in_view` runs on every guard check in any mode and inside the hold. It logs
  `staff_sighting` and raises `gm_suspected` at once (`suspect_staff`: one per open alarm). The
  sighting is queued (`pending_staff`), and the next work-mode speech check holds for it
  (`speech_hold`; the summary reads "… is in view (invulnerable player …)").
- `loop_hunt` shares the plumbing: `new_speakers` adds the sightings, so the hold follows the hunt's
  rules (deferred while fighting, survival overrides). The alarm and `staff_sighting` come at once.
- Tests: `test_speech_guard.py` `test_sightings`; `test_loop_lumber.py staff_in_view` (a vendor at
  1 tile raises nothing; then a robed notoriety-7 + `0x20` mobile gives one `gm_suspected`, one
  hold, one `staff_sighting` with the robe, nothing sent until both acks, then the trip banks);
  `harness/test_staff_sighting_replay.py`. That last one replays the 10 committed captures (~2 s) and
  checks after every mobile 0x20, the only packet that sets notoriety or flags: 2,597 checks, 271
  notoriety-7 and 263 player-flagged mobiles, zero hints.
- Open: a GM shown blue/grey or hidden still gets past the sight rule (the speech hold and the
  other hints cover them). The first live `staff_sighting` will show whether the rule holds.

## One supervisor for the stack (decided and built 2026-10-04)

User request: one script that brings up the proxy, the viz, Laya and the rest, and supervises
them; the Telegram bridge on by default, no opt-in. Built: `harness/stack.py up|status|restart|down`
(docs/NOTES.md "Stack supervisor"). Before it, each service was started by hand in its own window
and nothing restarted one that died (the viz's native live-view crash, NOTES 2026-10-04).
- **One foreground process, children in their own process groups.** Health is the services'
  listening ports (read from `netstat`, so the supervisor never connects to the game port) plus
  laya's `/health`. A managed service that exits is restarted with a backoff and posts
  `service_down` (once per outage), `service_up`, `service_flapping`.
- **Stops go through each service's own Ctrl-C path:** a bootstrap maps SIGBREAK to
  KeyboardInterrupt, the supervisor sends CTRL_BREAK, then `taskkill /T /F` after a grace period.
  So the proxy still flushes the memory store; laya-serve.exe goes with its wrapper (the port
  owners seen while it ran are killed too).
- **Already-running services are adopted, not duplicated:** watched as `external`, replaced by
  the supervisor's own only once they go away. So `up` is safe next to a manually started stack,
  and `down` never stops what it didn't start.
- **The NAT stays elevated and outside:** a non-elevated supervisor can't own or restart it
  silently. It watches the lookup port, posts an urgent `nat_down` and reruns
  `start_proxy_nat.ps1` once per outage (one UAC prompt); `restart nat` asks again. Not
  self-elevating, by the rule in NOTES ("Never write a self-elevating `.ps1`").
- **Not managed:** the game client (the user launches it), the overseer session, the backup task
  (Task Scheduler), the Discord tooling (one computer only, opt-in).
- Rejected: **Windows services / NSSM** (a service runs outside the user's session: no console for
  CTRL_BREAK, and the viz live view and the GPU client need the desktop session) and **Task
  Scheduler restarts** (no health checks beyond "process exited", and no adoption).

## Rune libraries: the DTF guild house beside Cambria (decided and built 2026-10-04)

User: Outland Dan (guild DTF) is our first real lumberjacker; the rune tomes in the guild house
(a Witcher set plus tomes of useful places) are where we go for any place our own book doesn't
have, "similar to the Cambria rune library". Built (docs/research/WORLD_LOCATIONS.md §5):
- **One table of libraries, per tome its rows with their own landing tiles**
  (`harness/data/rune_libraries.json`); `witcher_runes.json` keeps only id → name and dig tile.
  Reason: the guild's Witcher runes land up to 100 tiles off the CSV tiles, and its named places
  have no table at all, so a tome row's own tile is the only truth. Rejected: a second Witcher
  `tome` field per library on the rune table (two conventions for the same "which tome" fact, and
  nowhere for named places).
- **Learn by reading, not by recalling:** each rune's detail page shows its tile, so `act
  read_tomes` reads a whole library (8 min for 36 tomes) without spending a charge or leaving
  the house. Rows are found by name at use time, as at Cambria.
- **Which library:** a Witcher spot's trip recalls out from the library nearest to where it starts
  that holds the rune (`places.library_for`), and the planner counts a spot as at hand from any
  such library's hub. So existing Cambria-discovered spots work for Dan from the guild house and
  for Hackworth from Cambria, with no copy per library. The spot's own `library` breaks ties and
  still sets its overhead prior. **Superseded 2026-10-04** ("Lumber trips end in the rental room,
  not at a bank"): a trip recalls out to the landing nearest the grove from the home library or
  the character's own books (`lumber_opt.landing_for`); `places.library_for` is gone.
- **Named places:** `ctl runes find|near` say which row lands nearest a destination and print the
  exact `act recall --library … --rune … [--tome …]`; 20 names repeat across tomes, so a recall
  refuses an ambiguous name instead of guessing.
- Open: the guild library's tomes are guild property; their charges are spent like Cambria's
  public ones (the runner prefers a charge, else Dan's own Recall spell). If the guild wants
  members to cast instead, `escape.recall(prefer="spell")` is the switch.

## Lumber trips end in the rental room, not at a bank (decided and built offline 2026-10-04)

User decision (clean cutover, no shims): the lumber loop never travels to a bank. Supersedes the
2026-10-01 bank decision (Phase 4 notes above). Every trip starts at home (the rental room or the
DTF guild house, `harness/data/homes.json` keyed by character), leaves the room through its door
("Exit to House Steward"), and goes out by recalling "as close to the lumbering spot as we can via
rune tomes in the rune library or our own rune tomes/books in our backpack, never public moongates
or starting at banks and walking" (user): the landing nearest the grove with a walking route into
it, dangerous ones left out (`lumber_opt.landing_for`, shared by the runner and `ctl lumber plan`).
Home is the own book's default rune (Outland Dan: "DTF Loot Chest"), in through the house steward
(`harness/room.py`), convert in the room, boards and the spent trapped pouch into the room's secure
chest. The run ends in the room. Why: the room is the safe place (no recall reaches it, nothing
converts in the field under attack), the boards collect in one secure container at home, no speech
and no banker, one travel rule for every spot. Removed: bankers in spots, `--bank-range`,
`open_bank`/`deposit`, `--recall off` (the home book is required for every spot: it is the way
home), Young-only Shelter. A PvP escape still stops at home outside the room (entry may be refused
for 2 min after PvP). Details: LUMBER_LOOP.md §12.5, §13 "Trip"; offline proof
`test_loop_lumber.py` (a simulated steward, room menus from the live gumps, door, chest).

## Jobs page date range (decided and built 2026-10-05)

User request: a date range selector on the viz Jobs page, with whatever SQLite indexes keep it fast
as the store grows. Built (docs/VISUALIZER.md §2.4, docs/MEMORY.md "Indexes for time-range reads"):

- **Range = local calendar days, inclusive, in the URL hash** (`#jobs?from=…&to=…`), turned into
  [since, until) epoch seconds by the browser. The server already had `since`; `until` is the only
  new parameter, so the server stays timezone-free for the range (the `tz` query still does the
  per-day split). Rejected: relative presets in the URL (`range=7d`) since a link would mean a
  different window tomorrow; the presets write dates.
- **Trips by `t_start`**, like `since` already did, so a trip never splits across ranges.
- **The range goes into SQL**, not only the Python filter: `Memory.episodes/job_events` and
  `jobs.harvest_rows` take [since, until). New indexes `episodes(loop, t_start)` and
  `harvest_attempts(t)`; `job_events(job, t)` already existed. `EXPLAIN QUERY PLAN` on the
  2026-10-05 store: all three are index SEARCHes (before: `episodes` and `harvest_attempts` were
  full scans). No schema bump: `CREATE INDEX IF NOT EXISTS` builds them on the next open.
- **The lumber plan stays all-history and is cached per minute** in `viz_server` (its seed is the
  minute). Profiling the 2026-10-05 store: one lumber `/api/jobs` took 3.4 s, of which the
  optimizer's Monte Carlo (`lumber_opt.plan`) was ~all and the SQL reads milliseconds. Without the
  separate cache every range click would rerun it; now a range change answers in 10–40 ms. The
  plan can lag new trips by up to a minute. The optimizer's own cost grows with spots × draws, not
  with the store, so indexes don't help it.
- **The plan then moved to its own route, loaded async** (same day, user request: the first page
  load still waited ~3.4 s). `/api/jobs/plan` with its own connection and lock, so the plan never
  holds the lock the chat and analytics share; the page renders from `/api/jobs` and shows
  skeletons in the two plan panels until the plan comes. Live store: KPIs at 0.2 s, plan at
  3.6 s. Rejected: streaming one response in parts (the server is plain `http.server` JSON) and
  precomputing the plan in a background thread (it would run every minute with nobody watching).

## Leave on faction tags and on precasts (decided and built offline 2026-10-06)

After the run-16 death at witcher_66 (docs/NOTES.md "PK death at a faction waypoint"), the user
asked for solutions to "a hostile player can just speak to freeze us in place, then kill us", then
chose two of the six proposed (F, G), and after run 5's death the same day also E. Left out: A (keep
distance during a speech hold), B (leave on a group of unknown players), C (a crowd rule without
speech) and D (a hold timeout).

- **F, faction tags:** out at a pvp spot, a player whose click echo carries an Outlands faction tag
  is a flee-level threat at any distance in view, like a red. Evidence: the title lines were in the
  store all along (357 lines, one hue per faction); 4 of 75 lumber trips had a tagged player in view
  and 2 of them ended in an attack; 3 of the 5 players who ever attacked Dan carried a tag. A
  faction waypost marker in view marks the spot as a faction zone for the planner.
- **G, precasts:** out at a pvp spot, a player saying a harmful spell's power words within 12 tiles
  is a flee-level threat. On run 16 the words came 5–7 s before the attack. Over 75 trips 6 had
  such words and 3 of those were followed by an attack. The proposal said "while nothing is fighting
  them". That filter was dropped because it could hide a PK who casts while a creature stands by;
  the cost is the other 3 trips cut short (a PvM mage casting near us).
- **E, run before the recall (added the same day, after Seer8's run 5 death at witcher_162: a lone red
  mage's Energy Bolts disturbed three standing recalls in a row, the first 1.26 s into the cast; you
  can't move while casting, Outlands Discord):** any player escape (a red, a flee-level player, an
  aggressor, F or G) with one of them within spell range (12 tiles) first runs until every one is
  `PLAYER_RECALL_GAP` (18) tiles off or out of view (`gain_distance` toward the escape goals, at most
  60 steps), then casts once; a disturbed cast means run again, then cast again. After 3 failed casts the
  guard flight follows as before. Nobody within 12 tiles: recall at once, standing, as before. The chop
  cursor is cancelled before the run. [INFERENCE: against a mounted mage holding a precast the gap
  buys little; it beats one who has to cast after closing in.] Scenarios `red_aim`, `faction`, `precast`
  check the first step away within 0.5 s and the book pressed 18+ tiles off.
- **Friendly:** players of our guild or our faction never trigger (our own tags come from our own click
  echo). The hunt runner is unchanged.

Code: world `runtime.title_tags` (Mobile/SelfState `faction`, `guild`), `threats.HARMFUL_WORDS`,
`harmful_spell`, `friendly`, `loop_lumber.field_players` / `note_waypost`, `lumber_opt.FACTION_ZONE_PRIOR`.
Tests: `test_world_units.py` `test_title_tags`, `test_lumber_opt.py` (faction zone), scenarios `faction`
and `precast` in `test_loop_lumber.py` (both failing before). Details: docs/LUMBER_LOOP.md §13
"Recall escape on players".

## After the witcher_336 death: pacers, hop risk, no-go areas (decided and built 2026-10-08)

User approval of Main's proposals after Seer-8's death report (a mounted gloomwood hunter's arrows,
every 2.26 s from 10 tiles, disturbed both recalls; Dan died 7 s after the first arrow with 1 028
hopped-in logs). Details: docs/LUMBER_LOOP.md §13 "A pacer" and §6 "Library hops".
- **Pacer recall timing (adopted):** a ranged creature that keeps its distance as we run isn't run
  from; the recall opens the book and presses the moment its next shot lands (a ~2.0 s cast fits in
  its 2.26 s interval). Rejected: running farther first (it kept 10 tiles at mounted pace); casting
  a heal instead (the cast is disturbed the same way). Risk named to the user: the margin is ~0.15 s.
- **Records (adopted):** hits while running before a recall and while it casts are `monster_hit` rows
  (`gap`/`recall`); the death cause comes from who hit us, not from who was in view.
- **Hop risk (adopted, user's option):** the hop's way out (landing to grove) now risks the carried
  load at the spot's death hazard, on both sides of the comparison. Its effect at that spot was ~0.3
  logs: the shortfall was the hazard estimate for an unexplored spot, not the formula.
- **No-go areas (user decision):** `lumber_opt.NO_GO_AREAS`, committed so every machine and every
  `lumber discover` honour it: Old Papua (200 tiles around 3654,2957, facet 0) is "unsurvivable for
  Dan without significantly higher aspect and better armor". The store's two spots there
  (witcher_336, witcher_318) are also set `disabled` on the desktop.
- **Runebook charges (no change):** the reads were right; the guild shelf's resupply refills the
  book to 10 (docs/NOTES.md "Storage shelves").

## Hits from just out of view: run, don't recall (decided and built 2026-10-08)

User, after Seer-7's run 1 at witcher_86: a gargoyle's Flamestrike from just out of view sent the trip
home ("taking damage, no creature in view that could have hit us"); "a human player would just have
moved out of range of the gargoyle, healed, and carried on". The events show its cast animation 1.4 s
before the strike and the world model pruning it at the 18-tile edge in between.
- **Adopted:** `threats.unseen_attackers`. With no candidate in view, monsters that left the view
  within 30 s (last_seen: range or delete), last seen within view range + 6, hostile or of unknown
  aggression, are the candidates at their last tile; one is run from like any single ranged attacker,
  and its body learns the distance as reach (so the next run keeps its trees that far from it).
- **Kept:** home when nothing was seen at all (a hidden player stays possible), on two candidates,
  and on every other recall rule (hits below the threshold, a re-hit within 10 s, a 4th return, …).
- **Rejected:** walking away from damage with no candidate at all (no direction to walk; the hidden
  player case), and seeding gargoyles as a ranged body by hand (one live hit teaches it).

## Graceful stop: `ctl stop --after-trip` (decided and built 2026-10-07)

User approval of Main's incident proposal. On the end-the-day order of 2026-10-07 the overseer ran a
plain `ctl stop` on a run that was already home converting: 561 boards were left unstored in the
trapped pouch, and the overseer reported them stored without checking.
- **Adopted:** `ctl stop --after-trip` writes the meta key `task_finish` (task_wrap.FINISH_KEY)
  naming the task and returns at once. The wrapper hands the task its id (env `UO_TASK_ID`) and
  clears the key at the end. The lumber runner reads the key every 2 s and winds down like a due
  break (`LumberLoop.ending`: no more harvesting, home, convert, store, no further trip or hop,
  exit 0). docs/OVERSEER.md §5 now says to end a shift this way and to check the backpack after
  any stop; a plain `stop` is for what can't wait (possible staff, a server restriction).
- **Rejected:** sending the runner a signal (Windows has no SIGTERM handler for a detached
  process the wrapper could use cleanly, and the meta key reuses the bus everything else uses);
  making plain `stop` graceful (the emergency stop must stay immediate). Only lumber runs honour it
  for now; `ctl` refuses it for other tasks rather than pretending.

## Healing on the run (decided and built offline 2026-10-06, not yet tried live)

User request 2026-10-06, inspired by their Razor 'PK Getaway' script for Outlands: while running,
pop a trapped pouch when paralyzed (`[pouch`), drink a cure potion when poisoned, a heal potion when
hurt, a refresh potion when stamina is low. Both of that day's PK deaths were ~100 hits lost in ~4 s
while recall casts were disturbed (docs/NOTES.md, witcher_66 14:08, witcher_162 16:33).

- **Adopted for the lumber runner:** `healing.FleeAid` chooses one item use at a time (pouch if
  paralyzed, cure if poisoned, heal if hurt and not poisoned, refresh if tired and running) from items
  lying directly in the backpack; `LumberLoop.flee_aid` uses it on every guard check while running
  (`gap`, `flee`, `escape`), once standing before the recall, and between recall tries
  (`escape.escape(between=...)`). Uses are 0.55 s apart and the runebook's double-click waits 0.55 s
  after the last one [INFERENCE: the server's 0.5 s action delay on item use; live, a book click 0.3 s
  after another action was ignored]. Standing, a heal potion only at ≤ 50 % hits (it delays the book);
  running, from 25 % missing. Thresholds and evidence: docs/LUMBER_LOOP.md §13 "Healing on the run".
- **World model:** the player's own poison (0x16/0x17 type 1 for self, which Outlands sends) now lands
  in `world.self.poisoned`; before, only other mobiles' poison was kept.
- **Not adopted:** healing spells while running (casting stops movement on Outlands and the cast is
  the recall's slot); bandages (Dan has no Healing skill, and bandaging takes seconds standing in
  reach); moongates (none where Dan chops; a gate's prompt and its destination are their own risk);
  Hiding (no skill). No change to the hunt runner or `ctl`.

Code: `healing.SelfCare` / `Aid` / `in_pack` / `care_spell`, `loop_lumber.LumberLoop.flee_aid` (check_guards,
recall_out), `escape.escape(between=)`, world `SelfState.poisoned`. Tests: `test_healing.py`
(`flight_aid`), `test_escape.py` (`test_escape_between`), `test_world_units.py` (self 0x17), scenario
`flee_aid` in `test_loop_lumber.py`. Live checks for the first run: docs/NOTES.md "Healing on the run:
live checks".

**Revised 2026-10-07 (user): the whole pack, and self care between chops.** Live, Dan took a ranged hit
to 75/100 at witcher_20 and chopped on without healing: the aid only ran during getaways, and it only
looked at items lying directly in the backpack, while all his potions sat in a bag. The user pointed at
Razor: their heal script (`if poisoned` → `findtype "Orange Potion" backpack` → `dclick`; `if hp <
maxhits` → `potion "heal"`) finds potions at any bag depth and drinks by serial.
- **Adopted:** `healing.in_pack` searches the backpack at any depth (Razor CE `findtype … backpack` =
  `Item.FindItemsById(recurse: true)`; `potion` = `PlayerData.UseItem`, recursive over the client's
  known containers, one 0x06 by serial, no bag opened). The earlier "no bag is opened mid-flight"
  limit was ours, not the client's: the sanctioned assistant built into the Outlands client sends
  exactly this.

**Revised again 2026-10-07 (user): heal and cure like the PK Getaway script, always, recalling instead
of its moongates.** The user's words: "Healing should always be using a potion if its not on cooldown,
and otherwise keep using heal and greater heal as appropriate, like the PK escape script does … the
only real difference … is that we don't try to take a moongate like this script does; we try to recall
as appropriate." The script (Jaseowns' 'PK Getaway', kept in the session paste, not committed) loops:
bandages if Healing, `[pouch` if paralyzed, a cure potion if poisoned, a refresh at `diffstam >= 5`, a
strength potion below 100 Str without the buff, and with Magery ≥ 60 a heal potion, then Heal / Greater
Heal from 15 / 30 missing with mana ≥ 12, Cure by spell while poisoned; without Magery a heal potion at
any loss. Moongates and an overweight gold drop serve its escape.
- **Adopted (`healing.SelfCare`, `care_spell`; LUMBER_LOOP.md §13 "Self care"):** potions at every
  guard check, getaway or not (outside a chop attempt, a cast, a speech hold or a target cursor): pouch,
  cure, heal at any hit missing whenever off cooldown, refresh at 5 stamina missing, strength below 100
  Str. Between chops, when no potion went: Cure while poisoned, else Heal / Greater Heal from 15 missing
  (Greater Heal by `healing.choose`'s mana break-even, the hunt runner's rule, 25 missing at Dan's
  Magery 80.2), keeping Recall's 11 mana for the way home. Our recall decisions are unchanged.
  This replaces the thresholds of the first two versions (25 % running, 50 % standing, heal potions only
  between chops).
- **Not adopted:** spells during a getaway (casting holds the run [INFERENCE: RunUO
  Spell.BlocksMovement] and the cast slot belongs to the recall: "recall as appropriate"); the script's
  Heal-from-60 / Greater-Heal-from-30 split (it reads inverted; the break-even is about the same at
  Magery 100); bandages (no character has Healing: say so if one does); moongates and the overweight
  gold drop (we recall). The hunt runner keeps its own `--heal-at` rules (docs/HUNT_LOOP.md).

## Test suite speed (decided and built 2026-10-08)

User request: a faster test suite with every existing check kept. Serial, the 46 files took ~30 min
(captcha alone 17 min); `python harness/run_tests.py` now runs them in ~105 s (docs/NOTES.md
"Whole-suite speed").
- **Adopted:** speed up the code under test where the tests showed it slow, provably bit-identical
  (captcha numpy scoring with an exact rescore of the near-best candidates, table-driven S2C
  Huffman decode, world-model hot paths); skip-ahead clocks in the in-process tests whose time was
  sleeps; independent end-to-end scenarios and capture replays run concurrently; free ports and
  temp dirs everywhere so all files run side by side; a runner that starts the slowest first and
  caps worker pools so the CPU isn't oversubscribed.
- **Rejected:** shorter proxy/runner pacing or test-only fast paths in production timing (the
  cadence is what the server sees, ANTICHEAT.md); fewer samples, scenarios or iterations; float32
  captcha scoring (20 % faster but a looser exactness argument); retrying failed files in the
  runner (it would hide flakes). The e2e files now take as long as their longest real-time
  scenario (`test_loop_lumber` `main` ~100 s), which sets the suite's floor.

## Risks

- **Protocol drift**: Outlands patches frequently (client is days old at research time). Parser must be tolerant of unknown packets (log-and-forward) with a packet-ID registry that's easy to update.
- **Custom login/auth**: the OutlandsID HTTPS handshake is not yet mapped at byte level; proxy only needs the post-auth game connection, but settings/login responses may bind the session to the original destination — validate in Phase 1.
- **Behavioral statistics**: even perfect protocol emulation can be detected by play patterns; pacing, session length, and task selection are the mitigation (§8 rules).
- **Staff attention on Test Shard**: keep activity non-disruptive to avoid drawing staff notice (detection risk).

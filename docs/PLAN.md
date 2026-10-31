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
  - A heal potion whenever one can be drunk: not at full health, and 10 s since the last drink. A
    server "wait" refusal sets the clock to the wait it names ("You must wait 1 second…", live
    2026-10-11; a fresh 10 s had left Dan undrunk at 18/89 for his last 5 s) and falls back to a spell
    in the same call. The lumber runner's self care (`SelfCare`) drinks one only once the hits it heals
    are missing (user 2026-10-11: "we shouldn't drink a potion until we're missing as many HP as it will
    heal"; `healing.potion_heal`: a Greater Heal Potion's 25–45, mean 35; 20–35, mean 28, while PvP
    flagged). `ctl act heal` and the hunt runner keep the 2026-10-02 rule.
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
  - **Deferred (user, 2026-10-08):** Outland Dan has Detect Hidden 100, Tracking 100, Hiding 100.
    Passively, Tracking (Hunting murderers) already sees hidden reds within 50 tiles; an active use
    would add hidden greys/blues (thieves) within 8 tiles. Not built because no logs have been
    stolen since the trapped pouches (2026-10-04): 10 `thief` events, 1 `theft` (2026-10-06, one
    unexplained item, the 817 carried logs untouched). Proposed shape if it comes back: a use every
    15–25 s (jittered) between chops at a stand, logging what it reveals, the chop-rhythm cost (chop
    attempts are a median 4.9 s apart; estimated ≤ ~3 %) and any gap it leaves in Tracking's
    5 s Hunting checks (whether skill cooldowns are shared on Outlands is unknown: the main risk).
    Revisit if a hidden thief or a hidden non-red PK costs us.
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
  **Built 2026-10-10** (with Reactive Armor, user 2026-10-09; see "Wards kept up" below).

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
- **Reach:** `SMART_RANGE = 2` since 2026-10-08, measured from 4 768 `stand` events (docs/NOTES.md
  "Reach is 2 tiles"). It was 1 until then, the only value the capture proved (23:47: one tree
  within 6 tiles, at distance 1, faced). At 1 the trees 2 tiles off that a stand had emptied stayed
  candidates, and 25 % of stands said "nothing nearby" at the first attempt (~3.5 % of harvest time);
  a 45-min replay at 2 predicts 61 % of those with 4 false skips in 4 768 stands.
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
     Since 2026-10-10 that recall is one standing cast, then run-then-cast from the suspect ("Known PKs
     fled on sight; thief recall runs" below: a "thief" who closed again attacked and killed Dan).
   - `threats.py` changes from `watch` to this response for players in steal range during a harvest job.
   - Friends and guildmates: since 2026-10-09 greens (guild/alliance) are never thieves.
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
- `loop_hunt` shared the plumbing until 2026-10-15, when the hunt's speech hold went entirely (user:
  "GMs don't check attendance for dungeon crawling and stopping because a player speaks will definitely
  get us killed"): no speech reading, sightings or Laya in the hunt runner (docs/HUNT_LOOP.md "Guards").
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
History since 2026-10-21: home is Logan Wolf's house ("Home moves to Logan Wolf's house" below).

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
  guard flight follows as before. Nobody within 12 tiles: recall at once, standing, as before (since 2026-10-09 one cast, then run-then-cast: see "After the witcher_193 death"). The chop
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

## Tracked-red recall range 80 → 60 (decided 2026-10-08)

User: "reduce the distance where we recall out when a red is tracked to 60 or less". `loop_lumber`
`--track-react-range` default 60. Of the 24 tracked-red recalls on record (2026-10-03..08), 11 were at
61–80 tiles (64–77) and now become logged-only sightings; 13 at 35–57 still recall. Risk named: the
range went to 80 after Bastet, tracked at 55 tiles, struck mounted ~6.5 s later; 55 is still inside
60, but a mounted red at 61–80 tiles is ~1–2 s from that point, and the in-view red rule (any red
in view: recall) is the backstop. Reds beyond the range also stop counting as the spot's hazard.

## Allies are never fled from; witcher_59 disabled (decided 2026-10-09)

- **Allies (user: "Guild and alliance members should always be green to us and we shouldn't run from
  them"):** a player at notoriety 2 (green) is `threats.friendly` whatever tags he shows, so the faction
  and precast rules skip him, and he is never a `thief` at the steal guard. Reds stay reds (a murderer in
  our guild shows red, not green [INFERENCE: RunUO Notoriety.Compute checks murderers first]), and an
  actual attack (a swing at us, "… is attacking you!") still counts.
- **witcher_59 disabled (user):** its grove borders Prevalia's guard zone; at (1917,1749) the server said
  "Harvesting is not allowed in this area." four times and the run aborted (`lumber-20261009-222948-0e46`).
  Since 2026-10-13 the runner knows the outcome: that stand's trees are ruled out for good and the run goes
  on at the next stand (LUMBER_LOOP §13 "Outcomes"; user approved after witcher_26 the same way).
- **witcher_211 disabled (user, 2026-10-10):** its landing rune (3939,1159) met a brigand swarm on all
  three visits of 2026-10-09/10.

## After the witcher_180 death: run to the guards, any distance; heal spells in a getaway; Dispel Field (decided and built offline 2026-10-11)

Outland Dan died at witcher_180 (`lumber-20261011-163321-38d5`, 16:43:14-16:44:17): red Jimii Hendrix
came into view, and over a minute all 12 recall casts were disturbed (0.12-1.88 s into the cast) while Dan's
hits went 63 → 41 → 24 → 4 with 100 mana unused; the run toward 18 tiles ran into a player house's doors
(4235,803)/(4236,803) ("You are not allowed to access this.", cliloc 1061637, ~4 s lost); the guard flight
came only after the casts and found "no guarded place known within 250 tiles" (the nearest, Anchor's Rest's
bank, lies 440 tiles off and is reachable on foot: 688 steps). User decisions (docs/LUMBER_LOOP.md §13):
- **Run to the guards, any distance** (user: "ideally we'd run towards the nearest guard zone no matter how far
  away it is. Unless we're trapped on an island in which case our only choice is to recall"). The player
  escape's running now heads for the nearest guarded place reachable on foot from the start, in legs of 40
  route tiles, casting only while the gap (18 tiles) is open; arriving calls the guards. FLEE_MAX_DIST,
  FLEE_MAX_MOVES and PLAYER_RECALL_TRIES are gone. Reachability: a weighted A* (heuristic × 3) over the map
  (`guards.plan_route`), planned per spot before each trip (cached in the store; up to 150 000 expansions, ~7 s
  cold, which is enough to call Nusero Island an island) and from where we stand at escape time away from the
  spot (20 000 expansions, ~1 s; it reached 33 of 40 random spots' guards in 0.04-1.6 s). No route (an island,
  none known, undecided): run away and recall as before. Rejected: a whole-facet landmass map (29 M tiles at
  ~20 000 walkability expansions/s in Python: ~25 min and gigabytes; the per-spot cache gives the same answer
  where we work); the unweighted planner at escape time (300 000 expansions, 3.1 s for the death spot's route,
  beyond the Mover's 30 000-expansion plans); keeping the guard flight behind N failed casts (Dan's 12 casts);
  casting every 60 steps with him at our heels (each such cast was disturbed).
- **Heal spells in a getaway** (user: "GHeal takes longer to cast than mini-heal (In Mani) so mini-heals are
  harder to interrupt. Up to you how to choose which to cast"). Right before a player-escape recall cast, below
  60 % hits with no heal potion to drink now: Heal (0.5 s cast) when pressed (a foe within spell range or a hit
  within 2 s), else Greater Heal (1.25 s, 32-40 hits; Heal heals only 5-7 in PvP at Magery 80.2), Heal when
  Greater Heal can't be paid; Recall's mana and reagents kept; one cast, the recall either way. Rejected: Heal
  always (it can't keep up with 17-22 hits an exchange), Greater Heal always (its 1.25 s is where the casts
  broke), a spell when a potion can go (the potion costs no cast).
- **Dispel Field** (user: "PKs will also cast walls as Jimi Hendrix did (In Sanct Ylem) which we can dispel with
  the spell Dispel Field if they block our path"): the Mover calls the runner's `dispel_field` when fields cut
  its route with no way round; it keeps Recall's mana and black pearl. Doors and walls in the Mover itself:
  docs/NOTES.md.
- **Locked doors are walls at once** (user: "I don't think we need to remember that as long as the mover/world
  state updates quickly and treats the locked doors as walls"). The house had been recognised. The 'are we
  inside it' test was a 2D rectangle, and Dan's tile on its rim (and the outside stairs) counted as inside. It is
  now a flood fill with the doors as walls. A door answering 1061637 is walled for the rest of that walk, and
  nothing is kept afterwards (docs/NOTES.md "Locked house doors and spell walls").
- **Running away from players:** `escape_tiles` took its direction from creature zones only. With a player
  alone it ran due east whichever side he came from; the players' positions now count too.

## Wards kept up: Magic Reflection and Reactive Armor (decided 2026-10-09, built 2026-10-10)

The user believed Magic Reflection was already a required step of the lumber loop, but it never was: only the
overseer had cast it by hand on Hackworth (2026-10-03), and none of Outland Dan's trip rows show the buff. The
user asked for Reactive Armor too ("cast it to keep it active when we're lumbering"). The runner now casts
every ward that's down at home before the recall out, and recasts one per chop between chops once a ward is
used up (docs/LUMBER_LOOP.md §13 "Wards"). Both always keep Recall's mana and reagents. Rejected: casting
right after the landing. The arrival is when a camper strikes, and the landing's cast slot belongs to the
escape recall. Dan has Magery 80.2, so Magic Reflection (100 % at 70) can't fizzle and Reactive Armor
absorbs 20 physical damage.

## Known PKs fled on sight; thief recall runs; Report Murder declined (decided and built 2026-10-10)

Dan died at witcher_105 (14:59:23, `lumber-20261010-141818-e65c`). The blue **gorilkaenjoyer** (0x00006A2E,
mounted, who had crowded Dan as a "thief" at witcher_178 on 2026-10-07) came to 2 tiles at 14:59:05; KeepAway
stepped Dan off. He closed to 2 tiles again at 14:59:13 and the thief rule recalled standing. He attacked 0.6 s
into the cast ("gorilkaenjoyer is attacking you!" 14:59:14.68) and disturbed all five standing casts in 9 s
(-23, -24, -29, …; Reactive Armor absorbed its 20). Dan died at 77 → 0.

- **Known PKs (user: "Do we flee known PKs on sight? If not we should"):** we did not. Only reds, hostile
  (grey) players, faction-tagged players at PvP spots, precasters and current attackers were fled. Now any
  player who ever attacked one of our characters, or whom the user or the overseer names, is a known PK
  (`harness/known_pks.py`, `ctl pk`): fled on sight at any distance in view, whatever his notoriety. Greens
  are exempt, because the ally rule of 2026-10-09 is explicit. The 11 attackers on record were backfilled.
  Rejected: matching by serial only. The server's attack line names the player without a serial, and
  names were all the backfill had.
- **The thief recall (user picked "fix it too"):** one standing cast, then the player escape's
  run-then-cast from the suspect (`run_and_recall(players)`), never escape.escape's standing recasts. This
  is the same lesson as option E (2026-10-06) and mauna kea (2026-10-09), which the thief path had missed.
- **Death cause:** `record_death` now reads "… is attacking you!" lines heard inside the escape. The
  10-10 death had said "unknown"; job_event 8812 was corrected to "pk".
- **Report Murder gump: always decline** (user). `act gump` refuses Accept on gump 0x1F3940C6;
  OVERSEER.md §5 says to decline each one.

## Roaming creatures boxing a grove out (decided and built 2026-10-09)

Seer-14's witcher_181 leg: ~30 trees dropped for roaming ants' and a cougar's zones on 40–150-step walks,
no logs for 2 min 50 s; the user saw Dan "bouncing around too much". Proposed and approved ("do both"):
- **Stall rule:** no logs for 90 s with 5+ trees dropped for creature zones ends the harvest as
  `creature_blocked` (library hop or home).
- **Far-tree filter:** a tree over 30 tiles off with a creature in view within its zone + 10 tiles waits
  while any other tree is free.
Rejected: counting replans of one walk (`boxed_in` already does; here each walk was a new tree).

## After the witcher_193 death: red range back to 80, faction blues watched, run between player casts (decided and built 2026-10-09)

Seer-13's shift ended in a death (`lumber-20261009-135029-f526`, 14:25): red "mauna kea" was tracked at
68 tiles ("beyond 60: logged only"), came into view mounted at 18 tiles 10 s later, and disturbed six
standing recall casts in 8 s (none within 12 tiles at sight, so no run first; no guarded place within
250 tiles). User decisions:
- **Tracked-red range back to 80.** The 60 of 2026-10-08 is undone; at 80 the recall would have begun at
  the 68-tile hit.
- **Faction-tagged blues watched, not fled from** (user: "we're recalling away from blues too much"): 8
  recalls since 2026-10-06, each a lone blue at 12–18 tiles, none attacked. Now home only for two or more
  tagged players, a faction zone, one within 12 tiles (the user's pick over 8) or one closing in by 3+
  tiles. Kept: precast words within 12 tiles, attacks, reds.
- **Run between player-escape casts.** No standing multi-cast against players: one cast at once, then
  run-then-cast; past 3 casts the guard flight only when a guarded place is known, else run and cast on.
  [INFERENCE] Against a mounted red that keeps pace the run may gain little; it moves Dan out of melee
  reach between casts and lets potions work, which standing did not.

## Enough hatchet uses for every trip (decided and built 2026-10-08)

Seer-11's run 2 (`lumber-20261008-211509-2653`): Dan's only hatchet broke 1528 logs into a trip and the
run aborted; the shelf loadout holds 1 hatchet and a worn one can't be restocked, so resupply never
replaced it. User: "carry enough hatchets to complete the max chops the trip would require"; on how
to get more, "Both":
- **Adopted:** before each trip the runner reads each hatchet's uses (a single click) and needs
  quota / 5 uses (5 = the fewest logs a success has given); short, in the room, the most worn one
  goes into the room's chest and the shelf hands the loadout's fresh one, repeated until enough;
  still short, a juncture and the trip goes on. A hop the hatchets can't cover goes to the room
  first. The user raises Dan's loadout to 2+ hatchets in game, so a worn one is kept and topped up.
- **Rejected:** retrieving extra hatchets through the shelf's Retrieve menu (an unverified gump);
  cutting the trip quota to the uses left (the user wants full trips).
- **Cost:** a swapped hatchet's leftover uses sit in the chest (a few hundred at most).

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

## Razor hotkeys page in the viz (decided and built 2026-10-11)

User request: a viz page "Razor" that draws the keyboard and shows and edits the Razor hotkeys
(scripts, macros, items, spells, skills, agents, dress lists, Outlands commands) like Razor's Hot
Keys tab, with a better UI. User decisions: write the profiles in the install dir (an explicit
exception to AGENTS.md Constraint 1, Profiles/*.xml only); a catalog from upstream Razor CE plus
the Outlands files with a confirmed/unconfirmed flag per action; lock profiles in use, and all of
them when that can't be told; TKL + mouse layout, numpad bindings listed under the board. As built:
docs/VISUALIZER.md §2.15; Razor facts: docs/NOTES.md "Razor profiles and hotkeys".
- **Adopted:** splice only the `<hotkeys>` block (byte-identical elsewhere), optimistic
  concurrency on the file's sha256, a backup per save under `logs/razor_backups`, temp + replace.
  Write access from a one-time elevated `grant_razor_profiles.ps1` (Modify on Profiles only).
- **Rejected:** editing a profile Razor has loaded (Razor rewrites it from memory on logout/exit,
  silently undoing the edit); re-serialising the whole XML with ElementTree (changes bytes outside
  the hotkeys and risks the passwords element); running viz_server elevated (everything it serves
  would run as admin); a hand-maintained full action list (spells, skills, scripts, macros, agents
  and dress lists come from the install and the profile, so they can't go stale).
- **Risk:** inferred/unconfirmed actions that this Razor build doesn't register are dropped by
  Razor on load; if one vanishes, remove it from `harness/data/razor_hotkeys.json`.

## Nystul the Wizard: an AI assistant in the viz (decided and built 2026-10-08)

User request: an "AI Assistant" chat in the viz that answers the operator's questions by looking
things up (memory store and knowledge, Discord KB, live state, docs, logs), as a coding agent does
here, with the persona Nystul the Wizard. User decisions: Sonnet with thinking medium; open to
anyone who can open the viz (like the Seer chat); a full `#nystul` page plus a compact Live-page
panel on the same conversation; local sources only (no web search); a small built-in markdown
renderer; full roleplay with exact, sourced facts. As built: docs/VISUALIZER.md §2.13.
- **Adopted:** one headless `omp` run per question (`harness/nystul.py`), whose only tools come from
  a project extension (`harness/nystul_ext.ts`) that execs `harness/nystul_tools.py` with an argv.
  That script is the read-only, secret-free boundary (sealed paths, `mode=ro` + `query_only` + an
  authorizer for SQL, a read-only `ctl` subset, an allowlist of viz GET routes). Conversations in
  their own `harness/data/nystul.db`; the UI polls.
- **Rejected:** omp's built-in `read`/`grep`/`bash` tools: they can't be confined to paths, so a
  prompt-injected LAN user could read `settings.json`/`telegram.json` or run `ctl act`. Using
  `ctl know search` for the knowledge lookup: it posts a `memory` chat row on the Seer's bus and
  bumps `overseer_heartbeat`, which would fake "seer active". Storing conversations in the memory
  store's `chat` table: it is the Seer's bus. A long-lived omp RPC session per conversation: more
  moving parts for no gain at this volume; the transcript (12 messages, ≤24k chars) rides in the
  prompt file instead. Web search: user decision.
- **Risk:** the boundary is `nystul_tools.py`; any tool added to the extension must stay read-only
  (omp's `--tools` doesn't filter extension tools, docs/NOTES.md "Nystul").
- **Codex changes with an Approve button (decided and built 2026-10-08, user request):** a wrong
  Discord fact came up in a chat (a five-flamehound "summoner" team that the raw chat shows is a
  taming team), and the user wanted Nystul to fix it. Adopted: Nystul's `uo_propose` only checks a
  change and writes nothing. The viz shows it as a card with an Approve button, and only that click
  makes viz_server apply it (`harness/nystul_memory.py`), with a `system` memory row in the Seer's
  chat. The model never holds a write path, so a prompt-injected run can at most produce a card
  someone must approve. Rejected: a write tool for Nystul (any run, injected or not, could change
  what the Seer recalls); `ctl know` from viz_server (it bumps the Seer's heartbeat); editing the
  Discord KB (its facts are rebuilt by `discord_kb.py run` and it has no override; a `user` Codex
  entry that overrules the fact does the job).

## Wiki, patch notes and forums in the knowledge base (decided and built 2026-10-08)

User request: the Outlands wiki, the forums and the patch notes in the knowledge base (the
`knowledge` table behind `ctl know search`). User decisions: **hybrid** ingestion, and the forums'
**game-info sections** only; then, when the measured forum volume put the LLM pass at ~$60–90 list
price for all ten of them (~10M characters, 3.5M in Patches alone), **six sections**:
Announcements, Active Development, General Discussion, Player Guides & Macros, New Player
Questions, Corpse Creek. Dropped: Patches (the same staff patch notes as the news posts, already
in as written: of its 370 threads since 2018-11, 260 have a news PATCH post within 2 days; only
the 62 pre-launch threads before 2018-11 are forum-only), Suggestions & Ideas, Bug Reports,
Client & Launcher Support (low factual yield). Operation and numbers: docs/NOTES.md "Outlands
wiki, news and forums".

- **Capture: `harness/outlands_web.py` → `harness/data/outlands_web.db`** (gitignored; not backed up,
  a recrawl rebuilds it). Each site through its cheapest stable interface: the wiki's MediaWiki API
  (`action=parse`, rendered HTML, because 1 366 of the 2 139 articles are under 100 bytes of
  wikitext that templates expand: trait groups, creatures), the news site's WordPress REST API (all
  653 posts, 240 of them PATCH), and the XenForo forum's HTML pages (no public API). Paced GETs
  (1–2 s per request on the wiki/news, 1.5–3.5 s on the forum), browser User-Agent; incremental
  (wiki `touched`, news `modified`, a thread's last post time and reply count).
- **Wiki and news go in as text, not through an LLM (`harness/web_kb.py`).** They are the
  authoritative layer (staff-written; the wiki is kept current), so one entry per section chunk
  (cut at headings, ≤ 1 200 characters) keeps every number and table row and costs nothing. Topic
  = page and heading path, or the post's date and title for news, so old patch notes read as
  history beside the current wiki. Source `wiki` (0.7) / `doc` (0.8), importance 5, tags `wiki` /
  `news` + `patch|event|news`, ref `web-kb:<key> <url>`. The sync keeps per-key state, supersedes a
  changed section (history kept), retracts a vanished one only if nobody confirmed it, and never
  re-adds what the overseer retracted (same rules as the Discord promote). It embeds the new entries
  itself (GPU) so the first `know search` after a sync stays fast.
- **Forums go through the Discord KB pipeline (`harness/forum_kb.py`).** Forum posts are player
  chatter of mixed age (2017–2026), like Discord, so only official/consensus facts reach the store.
  `discord_kb.py` got a pluggable corpus (window building, official test, refs, digest header), and
  forum_kb supplies the forum one: one thread per window (long posts split, not cut), official
  = a staff post in Announcements / Active Development, tag `forum`, ref
  `forum-kb:<fact> <post link> (<first>..<last>)`. **Facts last seen before 2023-01-01 aren't
  promoted**: Outlands overhauled dungeons, PvP, harvesting and more in 2022–2024, and an old
  forum consensus has no newer claim to mark it outdated. They stay in the digest
  `docs/research/FORUM_KB.md` (committed) with their dates.
  **Only clusters with ≥ 2 authors or a staff claim are adjudicated** (user decision after the
  extraction cost $27.66 list price, 2026-10-08): 6 440 of the 7 768 clusters had one author, can
  never become consensus or official, and would only have added single-source lines to the digest
  (≈ $19 more). The corpus attribute `adjudicate_min_authors` (Discord 1, forums 2) sets it.
- **Rejected:**
  - Everything as raw chunks, forums included: 2018 forum answers would sit in recall next to vetted
    facts, the thing the Discord design avoided (user choice).
  - Everything through the LLM: loses table and number detail of the wiki and patch notes, and
    costs more (user choice).
  - Forum claims clustered together with the Discord claims in `discord_kb.db` (cross-source
    consensus): that DB's canonical copy lives on the Discord capture computer (the laptop); this
    desktop holds a read-only restored copy, so writing it here would fork it. Kept separate:
    `harness/data/forum_kb.db` (backed up by `backup.py` as `forum_kb/`).
  - Scraping the forum through a browser: plain GETs of public pages work (no login, no bot
    challenge seen), so a browser adds nothing.
  - Chunks of news posts merged per post: one patch post covers a dozen unrelated systems, and a
    mixed chunk's embedding matches none of them well; section chunks are what recall needs.

## Several characters at once (decided and built 2026-10-08; live check with two clients pending)

Several UO clients (different accounts/characters) run through the one proxy at the same time.
Before, `InjectionHub` kept only the last-attached session and the store didn't know which
character a row belonged to.

- **A character is its player serial** (`char_serial INTEGER` in the store, from S2C 0x1B; the
  runtime now emits `login_confirm {serial}`). Names are only selectors: one syntax everywhere
  (`harness/charsel.py`): `0x…` or all digits = serial, anything else = name, compared with
  `casefold`. Used by the state port's `char` field, the control port's `@char <sel>` frame,
  ctl `--char` (default env `UO_CHAR`), runner `--char`, viz `?char=`.
- **Proxy resolution** (`InjectionHub.pick`): no selector and one session = that session (old
  behaviour unchanged); no selector and several = an error naming them; several matches (an old
  connection not yet closed) = the newest. New state-port op `sessions`. Session log tags are
  unique (`<stamp>_2` when two clients connect in the same second or the stamp exists).
- **Store scoping, not separate stores.** `episodes`, `job_events`, `junctures`, `chat` gain a
  `char_serial` column (NULL = unscoped). A reader scoped to S sees S + NULL rows, so gate
  breaks, stack service junctures and Telegram user messages reach every character's overseer.
  World facts (walk moves, harvest nodes, teleporters, guard points, knowledge) stay shared: they
  are facts about the shard, not the character. The Jobs page stays aggregate.
- **Per character:** overseer heartbeat, juncture and chat cursors, `heal_potion_t`,
  `room_entered_via` (meta key `<base>:0x%08X`). **Shared:** the agent gate (one human
  multiboxing: one budget, one break schedule), captcha mode, the GM alarm, Telegram cursors.
- **One task per character, one overseer per character.** A task with a character conflicts
  with live tasks of the same character or with none; a task with no character conflicts with
  every live task, which keeps the one-task behaviour when nobody passes `--char`. Stop/finish
  requests became per-task meta keys.
- **Viz:** one server, a character picker in the header (live mode); per-character state
  pollers are created lazily per `?char=`.
- **Rejected:** one proxy per character (two NAT/divert setups, two state ports for every
  consumer to know); a store per character (world facts would fork, and the Jobs page couldn't
  aggregate); keying by account or name (an account has several characters, and names can be
  reused; the serial is what the server uses).

## Lumber optimizer review fixes (decided and built 2026-10-11)

User request: review the Bayesian optimizer for soundness against the recorded mechanics and whether
more evidence leads to better outcomes (report: `OPTIMIZER.md`), then implement all six recommendations
and bring the stored data in line. Built in `harness/lumber_opt.py` (docs/LUMBER_LOOP.md §6):

1. **Logs per success rescale.** Each trip's field time × its logs/success ÷ today's (newest 1 000
   successes). Rejected: a fitted time trend per character (log λ = spot + f(t)); the per-trip ratio is
   observed directly, needs no model of the aspect, and leaves the 14-day half-life to competition.
2. **Capacity from dry trips.** A dry trip's successes over (successes per tree × the trees available
   when it started) updates the spot's yielding share, worth ≤ 30 tiles; logs per tree = successes per
   tree × today's logs per success. Rejected: per-stand yield records (a runner change, and the server
   still doesn't say which tree); censored non-dry trips (their lower bound adds little and needs a
   censored likelihood).
3. **Regrowth from stand pairs** (the runner's `stand` events) added to the per-tree pairs. Kept the
   fit as is: the runner's own window censors gaps below it, which is accepted and documented.
4. **Deaths:** a death inside a trip (≤ 60 s after its end) is the spot's wherever it fell; the walk in
   from the landing and the lockout are exposed (`trip_terms(t_in=)`); the recovery is measured (death
   → next trip, ≤ 3 h, shrunk to 20 min by 2 deaths); the armor suit (1 000 gp, user) and hatchet
   prices (verite 5 000 gp, user; others by tier, user's choice) enter the gear at risk; unpriced gear
   is listed in the plan (`needs_prices`) and its chat line.
5. **Priors:** the λ prior's between-spot spread from a random-effects moment fit, CV floor 0.35 →
   0.15; sent home split into player and creature hazards with their own pooled rates; a shrunk
   time-of-day factor per 6-hour UTC block on the death and sent-home hazards.
6. **`ctl lumber audit`:** the prequential calibration from the review, per day.

Not changed: Thompson sampling itself (the user's decision on exploration vs. variety is open in
OPTIMIZER.md). Data (2026-10-11): 10 prices recorded (`ctl lumber price`), 7 lumber deaths without a
runner `death` job event backfilled from the proxy's `death` events (`backfilled` in their data); the
store was copied to `harness/data/harness-pre-optimizer-20261011.db` first (gitignored).

**Follow-up, same day (user: "do #2 and the forward projection from #1 now, and start recording aspect
tier and experience per trip"):** the field rate was still under-predicted 2–19 %.
- *Character drift* (`drift_levels`): one shared level per UTC day on top of each spot's rate,
  weighted least squares over the spots chopped on 2+ days, the level smooth (second-difference
  penalty, so a steady trend is free) and carried along its last change for up to a day. Rejected:
  a first-difference random walk (shrank a 5 %/day trend to ~1.5 %/day in the test), a plain
  shorter half-life (forgets spot evidence to chase the character).
- *Logs per success projected* to now along a 72-hour least-squares line (rising trends only, ≤ 10 %
  over the newest-1 000 mean). Rejected: a shorter window (noisier, still lags).
- *Aspect tier and xp per trip*: the runner reads the [aspect menu once per trip with the suit worn
  (`--aspect-read`, ~6 s at home) into `harvest_aspect.armor`. Not yet used by the model: logs per
  success from tier/xp needs days of rows first.

## After the Thugnificent death: tracked reds within 100, known PKs at any range (decided and built 2026-10-14)

Outland Dan died at witcher_52 (`lumber-20261014-141733-90b4`). Tracking had the known PK Thugnificent at
97 tiles 14 s before sight; it was logged only (react range 80). Three recalls were then disturbed. The
same day lxl's reflected spell stopped a run at the landing during the hop decision, and lxl went
unmarked. The user approved four changes:
1. **Range 100.** `--track-react-range` 80 → 100. In the store, 7 of the 75 hits beyond 80 were reds at
   81-99 tiles who came into view within 2 min (two of them killed Dan). Expected cost: about 5 more
   recalls a day.
2. **Known PKs at any distance.** A known PK found by Tracking triggers the red escape however far away.
   Rejected: a separate, larger range for known PKs. The user chose any distance.
3. **Hop decision like the room.** `try_hop`'s plan and hatchet clicks run settled and in mode
   'salvage'. This replaces the 2026-10-13 Escape catch there, which salvage makes unreachable.
4. **Named attackers out of view.** `attacked_by_players` marks them as known PKs too, with the
   serial from `players_seen` when one matches.

## Fighting with pets: the hunt runner's `--pets` (decided and built offline 2026-10-12)

The user played a Mausoleum trip on Logan Wolf (tamer: phoenix + gravebug) and asked for "a decent
combat runner" learned from how he fought (docs/NOTES.md "Logan Wolf's tamer fighting at the
Mausoleum"; docs/HUNT_LOOP.md "Pets"). Decisions:

- *A fighting style of the hunt runner, not a new runner.* Everything around the fight (recall in and
  out, leave rules, hostile players, speech hold, Stationary Penalty, crawl, loot rights, episodes)
  is the same job; only the fight, the healing and the pet bookkeeping differ. They live in
  `harness/tamer.py` (`Team`, hosted by `HuntLoop` like `crawl.Crawl`); `--pets` switches them in and
  picks the style's own default thresholds (`STYLE_DEFAULTS`). Rejected: copying `loop_hunt.py`.
- *Guard, never open.* The user (2026-10-12): pets on guard engage a mob that aggroes on us; an
  aggressive act of ours (a discord, "All Kill") turns the mob on the character. So the bard skills
  wait until the pets fight the target, and "All Kill" is kept for a mob already on us that the
  pets ignore. Logan opened with a bard skill in 9 of 60 target answers; the runner copies the
  advice, not that habit.
- *Bard order* discord → provoke another → peace, all from the trip's sequences; a provoked target
  isn't pacified. Rejected: Area (self-targeted) discord/peace: Logan never used them here, and an
  area discord also pulls every creature in 8 tiles.
- *Barding songs on by default* (`--songs`, 2026-10-13): the user sang all three on the second trip and
  said he had forgotten them on the first. Ground-targeted songs aren't aggressive acts [INFERENCE: a
  buff on us and the pets, knowledge #11226], so "guard,
  never open" holds. They go out as a care action, fights included, as he did. In the simulator they
  cut the pets' damage 8-23 % and pet-death runs by a fifth to a half (docs/SIM.md).
- *Veterinary supplies over bandages*: one double-click heals every pet in range and the player; its
  range is a setting (`--vet-range`), since the Healer's Codex "Without Borders" perk is being
  levelled (user 2026-10-12).
- *Pet commands Razor-shaped* (`actions.razor_say`): the server has only ever seen this player's pet
  commands from Razor (hue 0x03B1, double NUL). The same capture showed the agent's other speech
  off-hue for Logan Wolf; since 2026-10-17 the proxy stamps each character's own speech hue into
  it (ANTICHEAT.md §8.20). Rejected: sending "room"/"bank"/"[aspect" via `razor_say`, because
  Razor macros replay in the client's own hue and only its Say hotkeys use 0x03B1.
- *No release, ever* (user 2026-10-12): a fixed command list, and `ctl act say` / `menu_pick` refuse
  release/transfer.
- *A pet down ends the run* (`pet_down`), unless the vet supplies bring its ghost back first (2026-10-14:
  the user's vet loop resurrected GraveRobber on the run, capture 20261014_221341): "All Follow Me", vet
  the ghost up to `RES_TRY_S` 30 s, then leave. Rejected: a healer trip or a stable claim by the runner
  (the overseer or the user still does those).
- *Leave at 50 %, zones below 20 hits a minute* (2026-10-14, with moving on at once from cleared areas):
  the simulator's deaths rose 0.40 -> 1.25 a game hour with the faster pace, 46 of 54 on the walk to the
  gate after a leave at 35 % from the deep rooms; these two together took them to 0.54 at the same gold.
  It leaves more than "leave less, heal in place" (user 2026-10-13) asked; the trade is in docs/SIM.md.
- Not built: walking between dungeon levels (start on the hunting level), claiming/stabling, Herding.
  Verified offline only (`test_loop_hunt.py pets`); first live run pending.

## A Mausoleum simulator for the combat runner (decided and built 2026-10-13)

The user asked whether we could emulate the Outlands server and train the combat runner in it, then for
"a thorough Mausoleum simulator" (docs/SIM.md). Decisions:

- *Simulate the level, not the server.* The Outlands server is closed; a ModernUO/ServUO private server
  would be standard UO rules on wall-clock timers in another protocol dialect. Instead `harness/sim/` is a
  model of the one level Logan Wolf fought on, speaking the packets our world model reads, on the real
  map (walking by `pathfind.Walk`, sight by the new `los.py`, 61/61 against the live server's answers).
- *The runner is unmodified.* `sim.clock.install` points the harness modules' `time` at a virtual clock,
  `SimLink` stands in for the proxy's Link; rejected: threading a clock argument through 100+ call sites
  of live code. A state read costs game time, so polling loops progress.
- *Evidence for every number.* Creature stats from the wiki (knowledge ids; the capture confirmed hits
  and gold exactly), behaviour from the capture (`sim/calibrate.py`, sample sizes kept), the rest marked
  [INFERENCE] in the scenario's notes; the barding skill and the pets' threat weights fitted to the trip.
  `run.py --compare` sets the simulated rates beside the trip's.
- *What it is for*: regression and edge cases (it found three runner bugs on its first runs, docs/SIM.md),
  policy experiments (`--set`, runner arguments; e.g. the pet fall-back), and threshold sweeps whose
  answers are starting points for live checks, not defaults: the monsters' damage to the player is
  under-measured. Rejected for now: learning a policy from scratch (it would learn the sim's gaps).
- Captures that would close the gaps are listed in docs/SIM.md "Captures wanted".
- *Parity with the live runs* (2026-10-20, after the 18:22 death at an eerie corpse; docs/SIM.md "Parity with the
  live runs"). Since the first trip, ~40 live pets visits (5.5 h on the level) give the yardstick the trip couldn't.
  Decisions: (1) compare **the runner's own records** in both worlds (its visits and `fight`/`death`/`leave` job
  events, the same code writing them), plus the captures for what the runner doesn't record (damage on the pets,
  the monsters met), per hour on the level against a bootstrap interval over the live visits: `sim/parity.py`;
  (2) a simulated career **starts from the live memory** (`run.py --prior`), as every live run does, and keeps
  live-like game time between runs; rejected: longer empty careers (they never reach the live store's hours of zone
  knowledge); (3) recalibrate only where the live numbers have the sample (spawns from 37 visits' sightings, bard
  rates from 816 attempts, the phoenix from 134 kills), each with its evidence in the scenario's notes, and leave
  the rest as named open gaps; rejected: tuning free parameters until the table matches (it would fit noise and
  hide model errors). The Field of Souls corpses are in the world while the event runs (`--eerie`).
  The one exception, by the user's call ("Adopt the Tank draw", 2026-10-20): the live first contacts (the gravebug
  61 % where Logan was the nearest a monster saw) override the user's 2026-10-15 rule that such a monster goes for
  Logan; the simulator's monsters had gone for him at 29 %, which made it ~3x as deadly as live. `tank_draw` 0.9 is
  the one fitted parameter, to that measured share; rejected: leaving the rule and a 2.8x death rate.
  (4) 2026-10-21: the yardstick is **the visits of the runner the simulator runs** (`parity.RUNNER_SINCE`, since the
  crawl's seek of monsters in view, 10-17 23:08; `--since` for any other cut); rejected: every visit since the live
  server cut (several runner versions; the crawl gaps were mostly the older crawls). A rare count's interval is the
  wider of the bootstrap and its exact Poisson interval (the bootstrap can't exceed the visits with an event).

## Hunt job pane in the viz (decided and built 2026-10-17)

User request: a hunt job panel on the Live page like the lumber one, with gold looted per hour and DPS as
instantaneous rates (docs/VISUALIZER.md §2.16). Decisions:

- *Gold from the runner's own tally, carried on the intent.* `HuntLoop.doing` sends `tally` (its `totals`)
  and `started` with every intent (proxy fields, §2.3); the viz keeps one sample per change from the
  `agent_intent` events, plus the closed intent steps after a page load. Rejected: polling the store's
  `loot` job events (no character filter in `/api/jobs`, a 15 s refresh, and the run's start isn't in the
  store until a visit ends); the pack's gold (banking and buying move it; no history before the page).
- *DPS in the browser from the "-N" numbers,* split by the damaged mobile's notoriety (us and green =
  taken; hostile non-player creatures = dealt). No runner or proxy change, and the numbers carry exact
  times. Accepted limit: the numbers name no attacker, so another player's damage on the same monsters
  counts as dealt.
- *Windows*: gold and kills over 5 min (loot comes a corpse at a time, ~800 gold each in the Mausoleum),
  damage over 15 s.
- The proxy needs a restart to pass the new fields; until then the pane shows DPS and says the tally is
  missing.

## Field of Souls: the eerie-corpse alarm and soul jars in the hunt runner (decided and built 2026-10-17)

User request: "a spoooky alert sound when an eerie corpse is seen (once per corpse)", and "the hunt runner to
be able to fill soul jars on its own when we have a jar and an eerie corpse is found" (docs/NOTES.md "Field of
Souls"; docs/HUNT_LOOP.md "Soul jars"). Decisions:

- *The alarm lives in the proxy*, the one process that sees every live session whether a runner, the overseer or
  the user is playing. Rejected: in the runners (silent during manual play), and `ctl wait` (only while an overseer
  waits). It fires on the first recognition of each eerie corpse, one set for all sessions, and plays on a thread so
  the relay never waits for the 2 s sound. Replays (viz_feed) build their taps without it, so they stay silent.
- *An eerie corpse is "dead" art plus its eerie whispers a few serials on*, not one graphic: the corpse art is
  random (5 different ones in 5 corpses) and both pieces also occur alone (decor, spells), while the pair occurred
  only for the 5 eerie corpses in the store. The first version keyed on `0xB1F3` (the only one seen then) and was
  fixed the same evening, when the second corpse Logan used was `0xB202`.
- *A synthesized wail, not a file*: no asset to ship or lose, the same sound on both computers, and its shape is set
  in `alerts.EERIE_*`.
- *The runner fills before it loots but never leaves a fight for it*: players race for an eerie corpse and the fill
  is one double-click, so with nothing attacking us it goes before looting. While the pets are engaged only a corpse
  within 2 tiles counts: walking up to 18 tiles off would pull Logan away from his pets mid-fight.
- *One try per corpse*: what the server says to a failed fill was never seen, so a miss is logged with whatever it
  said and not retried.
- *Not built*: taking the filled jar to an Occult Merchant (60 minutes to do it). The overseer tells the user
  (docs/OVERSEER.md).
- *The name-match spirit alert keys on the click label* (user request, the same evening): the server names our
  spirit on entry, and the stock client single-clicks every mobile on sight, so the label with that name is the
  sighting, 43 ms after it comes into view. Per session in the proxy (each character has its own spirit), once per
  field session, a chime unlike the wail. Rejected: matching the name in the world model's mobiles on every packet
  (the label event already carries it; the mobiles are scanned only when the name arrives with the spirit in view).
- *The reaper heatmap shows rates per hour watched, not counts* (user request, the same evening: "map the reaper
  spawns ... a reaper spawn heatmap"; docs/VISUALIZER.md §2.17). We only see what comes within 18 tiles of us, so
  raw counts would map our own route. The map divides by our dwell time near each cell, from the `step` events
  the store already has. A spawn is a reaper first seen inside the view (≤ 14 tiles), not one walking in at its
  edge. Both instances share one frame, the Hard island being the Normal one shifted (874, −32). A cell pools its
  8 neighbours, because one run puts a spawn or two in a cell.
  - Rejected: corpse positions alone. They are 3–4 times as many, but most are reapers that died out of view.
  - Rejected: a new store table. Proxy events reuse the event log and its `ev, t` index.
  - Rejected: backfilling by `memory.py ingest`. It only replays captures not yet in the store.
- *The field countdown and its 90 s toll run on the proxy's clock from the spirit's naming* (user request, the
  same evening; docs/VISUALIZER.md §2.18). The naming comes 3.1 s after entering and the ejection 893 ± 1 s
  after it in all three runs. So `fos_field.FieldLog` keeps the session and the state port carries the time
  left. The viz never does clock arithmetic, so a phone on the LAN or a replay shows the right time. The toll is
  decided on the proxy's 0.1 s timer as well as on world events, so it comes even when the field is quiet.
  - Rejected: the filled jar's "[13m 30s left until ejected]" label. It only comes when the jar is clicked, and
    it was off by 3 s once.
  - Rejected: the viz playing the sound. Nothing would sound with the viz closed, and once per open tab with
    several open.

## Mining runner: smelt in the field, keep the ingots (decided and built offline 2026-10-19)

User request after the live hand-mining capture 20261019_200350 (docs/NOTES.md "Live hand-mining capture"): a mining
job like the lumber one, run by the overseer, with a planner choosing the spot. Built: `harness/loop_mining.py`
(`ctl run mining`), `harness/mining_opt.py` (`ctl mining plan|spots|spot set`), seed spots from the map
(`python harness/ore_nodes.py spots`, `harness/data/mining_spots.json`), `harness/data/loops/mining.json`, store
schema v7. Offline-proven (`test_loop_mining.py`, `harness/test_mining.py`); run live on Outland Dan since 2026-10-19. Decisions:

- *Smelt at the spot's natural forge; the ingots go home in the trapped pouch.* Ore weighs 2 stones, an ingot 0.025:
  smelting on the way removes the weight limit on a trip, as converting does for logs. Rejected: carrying ore home
  (a trip ends after ~310 ore for Dan) and a pack animal (none owned, thieves steal from it, docs/NOTES.md "Pack
  animals").
- *Drop the ingots off into the room's Resource Stockpile at each trip's end* (user 2026-10-19: "we can just
  'restock' the resource stockpile and target our own backpack"; "lumber does have to pop the pouch in order to
  turn the logs into boards, whereas the ingots go straight into the stockpile").
  - How: the stockpile's context-menu Restock, its cursor answered with the backpack (`dropoff.run` with one step).
  - The user did it by hand at 22:44:39 that day: 1090 ingots left the live trapped pouch, which stayed armed.
  - Then the storage shelf's Restock and Resupply run as for lumber (they used to be skipped because the shelf would
    take the live pouch holding the ingots).
  - Before: "keep carrying" (the first plan's choice, 2026-10-19). It assumed storing meant setting the pouch off
    as lumber's convert does.
  - A death now loses only the trip's ingots, not every trip's. The planner's death charge (half a stint of ore)
    is unchanged.
- *Store the ingots the way lumber stores its boards* (user 2026-10-20: "if we hop from spot to spot we don't go
  back to the inn room to store them, but when a trip ends or we head home and we have enough ingots that it's too
  risky to carry them back into the field, we store them in the stockpile. And when the overseer ends a run we store
  them then as well").
  - A trip's end, an abort's way home and `stop --after-trip` all end in the room, where the ingots are stored.
  - A leg cut short (dry, a threat's recall, every stand in a creature's reach) is home at the landing. There
    `mining_opt.hop_choice` weighs the load against the room's time:
    - hop: the ingots the room would store (ingots / 2, in ore) × P(death) on the way in (`LOCKOUT_S`) and over the
      trip's field time left, at the next spot's posterior death hazard. Raw ore rides along either way: the
      stockpile leaves it, so it doesn't count. Until 13:11 on 2026-10-20 it did, and a leg with 0 ingots went into
      the room to "store the 0 ingots".
    - store first: `STORE_FIRST_S` (60 s: the room and a new run; 40 s since 2026-10-21, the house) at the spot's
      expected ore/h.
  - The runner hops when the risk is no more than the room's cost. Otherwise the leg goes into the room, stores,
    and the trip ends there.
  - This is lumber's comparison (`lumber_opt.hop_choice`: the load at risk vs the room's time) without its trip-size
    terms, since a mining trip is sized by field time.
  - On the store as of 2026-10-20 noon (pooled death hazard ~0.2/h with no mining death yet; ~530 ore/h), a hop
    takes fewer than ~140 ingots with 40 field minutes left and fewer than ~500 with 10. As deaths stay at zero
    over more field hours, the hazard falls and hops take more.
- *Smelt only near full weight or before leaving the spot* (user 2026-10-19: "just before we get full on weight or
  leave the spot"). Triggers:
  - 40 stones free;
  - the leg's end: dry, time up, a break or stop request;
  - a plain abort at the spot, before its recall home.

  A threat's recall never stops at the forge, so its ore comes home raw and is smelted with the next leg's. Rejected
  the same day: a smelt after each stand once 40 ore was carried (commit 2a255fe), too often for the risk it covers.
- *With a forge in the rental room, smelt there; the field forge only for the weight* (user 2026-10-20: a small forge
  in the inn room "the runner can use to smelt ore at home"; homes.json `forge`).
  - In the room, before the store (`MineLoop.convert`), every carried ore stack is smelted at the room's forge.
    That covers a trip's end, an abort's or a threat's recall home and a leg with no hop.
  - In the field only the 40-stones-free smelt is left. The leg-end smelt and the smelt before a plain abort's way
    home are dropped. The ore rides home, or on to the next spot across a hop, and leaving a spot no longer walks to
    its forge with whatever ended the leg around.
  - `hop_choice` now counts the raw ore as part of what the room would store, since the room smelts it.
  - Without a room forge (another home) the field rules above stand.
  - Kept: the field weight smelt. Going home at full weight instead would cost a room round trip (~60 s,
    `STORE_FIRST_S`) every ~310 ore against a forge walk at the spot.
- *A spot is a forge with ore around it*, one per non-town overworld forge site with at least 20 node blocks within
  40 tiles, the richest of any within 40 tiles of each other. Rune names don't filter anything (user 2026-10-19:
  the "Urk Fort/Smugglers Den" landing at Prevalia Quarry was safe; `places.danger_hint` and its monster-place
  words were removed from the landings, `lumber discover` and the mining seeds alike; the guild's own "Bad Places"
  tomes still keep their runes out of the landings). Every generated spot is active (user 2026-10-19, "All 49":
  until then only the two Prevalia Quarry forges were, the rest parked as candidates awaiting approval); the
  planner's Thompson sampling explores them and learns which pay. A forge that answers "not near a forge" disables
  its spot from the runner (a store row).
- *The runner is a LumberLoop subclass.* The trip, the threat checks, escapes, the speech hold, captcha, the
  landings and the way home are the lumber runner's, proven over hundreds of trips; LumberLoop got class hooks
  (`JOB`, `SKILL`, `YIELD`, `STASH`, `POUCHED`, `CARRIED`, `HOME_DONE`) and methods (`setup_tool`, `leg_row`,
  `trip_done_text`, `remember_faction_zone`), and `main` split into `common_args`/`run_loop`. Lumber behaviour is
  unchanged (test_loop_lumber.py all pass; its fakes take the hooks via `loop_fake`).
- *Stands from the map, ore state by 8x8 block.* `ore_nodes.area_stands` lists the area's stands with the blocks
  each reaches; "nothing nearby" marks those blocks depleted (`mine_blocks`, regrowth window 60 min), "not allowed"
  rules them out for good. The next stand: most available blocks, nearer first. Separate tables (`mine_attempts`,
  `mine_blocks`, `mining_spots`) keep mining rows out of `harvest_attempts`, which the lumber planner reads unfiltered.
- *A small planner*: Thompson sampling over ore per field hour (Gamma posterior, prior 1300 ore/h from the capture),
  net of sent-home and death hazards; no trip-size or tool terms. A trip digs `--field-min` field minutes across
  hops; a leg that runs dry hops to the greedy pick (current spot excluded) with `--hops` left.
- *No Restock of the room's storage shelf after a mining trip*, unlike lumber: Restock took the three live trapped
  pouches out of the pack (docs/NOTES.md "Storage shelves"), and the live pouch holds the carried ingots. Spent
  pouches go into the chest instead.
- *The planner on the viz, as lumber's* (2026-10-20, user request: the Mining page showed no planner data).
  `/api/jobs/plan?job=mining` (`jobs.mining_plan`, sharing `jobs._plan_spot_extras` with `lumber_plan`) feeds the
  Jobs → Mining "Optimizer: next pick" and spots panels and the Live Mining job pane's spot name, landing,
  "Expected" and "History here" (docs/VISUALIZER.md §2.4, §2.19). Decisions:
  - The planner panels and "Expected" show ingots, 2 × the planner's ore (a smelt gives 2 an ore), with ore in the
    tooltips and a "field ore/hr" row; the Live now-rate, the spots-in-range table and the KPIs stay in ore, since
    they measure ore dug.
  - No gold/hr projection in the planner UI: the model scores ore, not metal value. Gold per active hour is
    already a KPI tile.
  - Without a home the planner still ranks the spots (every active one "no landing", no pick), as lumber does,
    so the dashboard shows the model for a store whose legs name no character.
  - Draws stay at `mining_opt.DRAWS` (2000): the dashboard plan took 0.19 s on the live store. Lumber's
    `PLAN_DRAWS` cut was not needed.
- *No tool at the start isn't a stop at home* (2026-10-23, shift DanMiningSeer): `run()` checked for a pickaxe
  (`hatchet()`) before the first trip's `resupply_home`, so with none carried it aborted ("no pickaxe worn, in the
  backpack or in a bag in it") before `hatchets_ready` could take one from the house chest (live: 4 runs that day,
  the last mining-20261023-172637-05d6). The start check now runs only away from home; at home the trip's
  `hatchets_ready` tops up (pickaxes from the chest, lumber's hatchets from the shelf and chest) and posts
  `low_supplies` and stops when there are none. Same fix for the lumber runner (shared `run()`).
- *Not built*: ore maps, pickaxe wear tracking (a broken pickaxe leaves the hand empty and the next dig equips the
  spare or aborts with none).

## Half the Mausoleum deaths: shy monsters, waits and loot walks (decided and built offline 2026-10-22)

Goal (user 2026-10-21): half the simulator's deaths an hour without significantly less gold, the simulator not made
easier; then (user, same night) "stay out of their aggro range and patrol the pets into them" for purgers and
blightmares. Evidence and every number: docs/NOTES.md "Half the deaths in the simulator".

- *Runner only; the simulator untouched.* Its two doubtful points (no shove into a monster at any stamina, a blightmare
  that follows us out of its sight) had no live evidence either way: noted, not changed. The shove was measured and
  fixed later that day (user: "in Outlands I can run through mobs when I have full stamina"; docs/SIM.md "Shoving and
  stamina").
- *Keep out of a purger's or blightmare's shot rather than out of their room.* A `--crawl-avoid` box over the north-east
  room gave nearly the same death cut with the gold held, but gives up the bound souls; `Team.cover`, the pets
  patrolled in (`--patrol-pull` on by default) and "All Kill" at once at one on us did it with more gold (+4 %).
  `--never-aggro` for both cut deaths but left pets behind (pet-death runs 4 -> 11).
- *No Mover wait where it can hurt*: a walk's wait for mobiles ends when we are hit; the fight's care walks never wait.
- *Bounded loot walks* (twice the distance + 8): a corpse beyond that is left.
- *Not built*: "All Patrol" at any idle target out of our sight (the user's report of wandering in the soul cells):
  in the simulator it patrolled 5x as often for no more kills and more deaths. Open until a live look.
- *Live*: untried. The §5 command is unchanged (the flag's default changed). Watch for "cover:", "hurt waiting" and
  "'All Patrol' to" lines and for corpses left as "only a N-step detour".

## No running from the elder vampires (user 2026-10-24)

User: remove the running from an elder vampire and the elder vampire countess. `Team.evade` is gone (clean cutover:
`aggro.View.chasing`, `tamer.EVADE_*`, the evade branch of `drink_on_the_run`); one in war mode on us or the pets is
fought like any other monster. Their sight stays no-go and they are still never pulled (`--never-aggro`'s default
unchanged): the user asked to drop the running, not the avoidance. Simulator (200 runs, the goal batch's arguments;
docs/NOTES.md "Elder vampires: no more running"): deaths 0.21 -> 0.19 an hour, banked 105k -> 106k an hour, pets
left behind 5 -> 2 runs, all within the noise. *Live*: untried; the §5 command is unchanged. Watch for fights with
"an elder vampire" / "an elder vampire countess" in the task log where "running until it gives up" was.

## Home moves to Logan Wolf's house (user 2026-10-21)

User: "We've moved from an inn room into our own house. … The runners that use DTF rune tomes will now
need to recall to the DTF guild house instead of just exiting the inn room to the house steward. … The
runners will now need to recall home to drop off instead of going to DTF and entering the inn room."
Clean cutover; supersedes "Lumber trips end in the rental room" (2026-10-04) as to where home is. Live
facts: docs/NOTES.md "Logan Wolf's house"; details: LUMBER_LOOP.md §12.6, §13 "Trip".
- *Home = the house* (`homes.json` `landing` 3575,3251,6 on facet 0; `home.at_home` = within
  `HOME_R` 6 tiles) for every character. Replaced: the `room` entry, `home.in_room` and the 60-tile
  `NEAR_LANDING`. Convert, stockpile, chest, the house forge, the drop-off and the shelf all at home.
- *The way home is our own book's rune that lands at home*: its default when that one does (the
  spell-on-book fallback while a book recharges goes only to the default), else the rune named in
  `homes.json` `rune`, "Logan's House", by name. Why both: Logan's tome defaults to the house; Dan's
  runebook keeps "DTF Loot Chest" as its default. Replaced: "home = the default rune" plus the steward.
- *Library rows recall in*: the own book's `library_rune` ("DTF Loot Chest") into the guild house,
  checked against its landing, then the walk to the tome. One more recall per library trip, counted
  in `overhead_prior_s`. Replaced: leaving the room by "Exit to House Steward". Own-book rows recall
  from home.
- *The guild shelf only when passing*: the house shelf before every trip; the guild shelf (the only one
  with recall scrolls for Dan's book) right after a library recall-in, when the house shelf left
  something out or the book is below `BOOK_FULL`. Why: no extra recall just to shop.
- *The hunt drop-off*: home after the recall (Logan's default), the walk to the landing tile, the
  drop-off. Replaced: the steward and the room. A drop-off that can't run (not at home, an item out of
  reach, a container that won't open) ends the run at home (decided 2026-10-21 after the first house
  trip went back in with 25.5k gp; "went on with the load" before): the gold stays home, the Seer and
  the user see a task_failed. The tomes in the wooden shelf are reached through it (`dropoff` step
  `in`): back from a trip the client has forgotten the shelf's contents. Rejected: remembering the
  tomes' container in the world model across the 0x1D (the client itself forgets it; a player opens
  the shelf again).
- *Mount*: no room revival of a ghost pet (a healer or stable master); a remembered mount not in view
  at home is "resting" as before: the house auto-stashes mounts like the guild house (user 2026-10-21).
- *Room code kept only as `ctl act room`* (`harness/room.py`): a generic overseer tool for any rental
  room; no runner uses it. The trip phase key `to_room` keeps its name (store history, the planners and
  the viz read it) and now means the recall home and the walk to the landing tile; `leave_room` is no
  longer emitted.

## A red on the Mausoleum walk in: gold gate or on through (user 2026-10-22)

Logan died at 18:53:48 on the walk in's second level to the red Botan. Botan had been in view from the first level
44 s earlier, and the runner ran its hostile-player checks only on the hunt level (docs/NOTES.md "Dead on the walk
in to a red"). Main asked the user how to leave from there. The user chose: on the first level, **flee to a gold
gate and recall home**; past it (no gold gates there, no known way back), **dash on to Maus 3 and out by the crimson
gate**. The alternatives were a dash on from the first level too (a longer way out, and a red follows through the
teleporters) and back to the first level for a gold gate (no return teleporter is known). Built: the checks run on
the walk in, `walk_in_threat` / `walk_in_leave` / `walk_in_rush` (docs/HUNT_LOOP.md "A hostile player on the way").
The flight switches gold gates when the way to the chosen one passes them. Simulator, the second-level replay
(`sim/fight1853.py level2`): 12 of 12 dead before, 0 of 12 after.

## A red in a dead end: past them at once (user 2026-10-22)

The 20:01:02 death in Maus 3's east wing came from the "away from them first" rule of 2026-10-21. It ran ahead of
three reds into the wing's dead end (docs/NOTES.md "Dead in the east wing's dead end"). Main put three options to
the user, with the 10:35 death of 2026-10-21 (going past two reds in the same wing) as evidence against going
past: past them at once, the pets on the red and then past him, or away first only outside dead ends. The user
chose **away first only outside dead ends**. In a dead end, where every way to the gate from the flee tile still
passes them, the flight goes past them at once with no rope away. Built: `HuntLoop.dead_end` in `flee_out`
(docs/HUNT_LOOP.md "Not into a dead end"). The user's other point was to rope when slowed even if attacked ("hinder
is a status that makes us walk slower"). The captures show the slow walk is Hamstring (icon 53) and Hinder (icon 19)
is a hold (docs/NOTES.md "Hinder, Hamstring and the rope"). Built: the rope while hamstrung, even with a player on
us (it jumped live during one). The rope's 2-minute combat cooldown is now known to the runner and the simulator:
the 20:00:58 rope was refused by it. During a hold (Hinder, Entangle, Paralyze, a transfix) the runner tries no rope
(user: "I'm sure the 'hold' conditions don't let us rope"). From ranged reds (spell words, a bow or crossbow, or
nothing in hand) the rope goes even while they hit us (user: "being near them for a second while roping is no worse
than being a few tiles away"). It lands along the way to the goal, the tile fewest of them see first. Main proposed
"out of their sight first" over every tile in reach; the dead-end replay lost 7 of 24 with it, so the landing stays on
the way. Magic resist potions were the user's other idea: the wiki says they don't work against player spells.

## The server's "cannot be seen" holds until someone moves (user 2026-10-23)

User: "The runner keeps trying to 'All Kill' a reanimated demon that is not in line of sight, and doesn't seem to
notice the 'target cannot be seen' system message". The runner did read the message, but its block was only 10 s.
Decision: a refused "All Kill" isn't sent again until we or the target move off the tiles it was refused from.
Rejected: gating it on the sight model (`los.Sight`). The server's answer is the ground truth, and a model false
negative would hold back the pets from a monster really hitting us. The same evening the crawl was found walking back
into a passed-over daemon's bone burst after each stand-off. A passed-over awake area hitter's reach is now a crawl
danger zone, like an awake blightmare's (docs/NOTES.md "'All Kill' at an unseen reanimated daemon").

## 'All Kill' ends guarding: guard again once its target is dropped (2026-10-24)

Logan died at 23:30:37 to a blightmare that woke on him while both pets followed him out of war mode: the runner's
"All Kill"s at a reanimated daemon they couldn't reach had ended their guarding, and dropping the daemon set nothing to
guard again (docs/NOTES.md "Dead with the pets off guard"). Decision: the target of an "All Kill" the pets took is
tracked; once it is dropped, passed over, out of view or dead, "All Guard Me" is due (`Team.kill_order_over`).
`sim/fight2330.py`: 3 of 96 seeds dead before, 0 after; the 20-minute batch no worse (deaths an hour 0.19 -> 0.15,
gold an hour +1 %). Rejected on the replay's numbers: cover never stepping nearer an awake shy monster (7 deaths in 48
against 3: no tile qualified and Logan stood in its shot), and "All Kill" at a shy monster on us ahead of care's other
actions (2-26 deaths in 48; our "All Kill" keeps its aggro on us). Open: cover near an awake blightmare, which live
followed him out of its sight at once.

## Every hold is a hold for the walk (2026-10-24)

An Entangle beside a door on the walk in aborted a run with Logan on Maus 2 (docs/NOTES.md "Entangled beside a
door"). The Mover knew only the transfix (a text line). Decision: the buff holds (Hinder, Entangle, Paralyze;
icons from the buff bar, the same ones the rope rule already used) are holds for the walk too: one source,
`Mover.held(st)`, which `HuntLoop.held_now` now calls. A door retry that the server took as a turn steps once more.
Rejected: walling a door only after two retries. Being held explains the denial, so the extra retry would only pass
when the hold happened to end in time.

## Escape home after an error (2026-10-24)

The walk in's "no route" at 00:40:32 ended a run with Logan alive on Maus 2. Nobody moved him, and a calamity killed
him there at 01:10:48 (docs/NOTES.md "Entangled beside a door on the walk in"). User: "From now on Overseers should
escape from the dungeon and return home when there is an erroneous stoppage like that." Decisions:
- **The runner escapes before it exits** (`HuntLoop.on_error`). Every error that reaches the top (an Abort, a
  WalkFailed, a crash) with Logan alive inside a no-recall dungeon gets him home first. The exit stays 1, and the last
  `ABORTED:` line names the error and the escape, so the wrapper's task_failed shows both. An `escape` juncture says
  how it went: attention once home, urgent when he is still inside. No escape after a death, a server restriction
  (its rule is stop), a paused gate (the user has him) or `ctl stop` (asked to stop). None either for an error of the
  escape itself: a second escape could loop. Rejected: leaving it to the Seer alone. The runner holds the state, the
  Mover and the pets' roster at the moment of the error; a Seer acts minutes later at best, and nobody acted for 30.
- **The way out by evidence, reusing the leaves.** The planner never routes over a teleporter, so each level has only
  the ways it can walk. The first level has its gold moongates (the walk-in leave's flight, `gold_gate_way`, then the
  recall). The hunt level has the crimson leave gate (`leave_by_gate`, then the tome from Totem).
- **The middle level (Maus 2) goes on, not back.** Of the two choices there, going on through the remaining
  `--enter-via` teleporters to the leave gate is the one the code can route. In the simulator, from (5507, 691) it is
  57 steps to 5522,637, then 38 from the Maus 3 arrival to the gate. There is no route back to a gold moongate: no
  teleporter from Maus 2 to the first level is known (not in the live store's 18 Mausoleum teleporters, not in the
  scenario), and the planner can't take one anyway. It is also the safer choice by the record: it is the walk in's own
  way, walked on every visit, and the way the user chose for a red met on Maus 2 ("Dash on to Maus 3, crimson gate",
  `walk_in_threat`). Walking back would need an unmapped teleporter search on a level where Logan stood alone. The
  pets get "All Follow Me" and no wait on the teleporters (`walk_in_rush`); the gate's walk then waits for them.
- **A failed way is tried again** from where it left him, 3 tries, 5 s apart. The live failure was a door walled for
  one walk, and the next walk plans afresh. Each try picks the level's way anew, so a rush that reached Maus 3 goes on
  by the gate.
- **`--go-home` for the Seer**: the same way out, or the recall from outside, then the drop-off, exit 0 at home.
  `ctl run` passes every argument through (`nargs=REMAINDER`, task_wrap's `argv`), so it needs no ctl change.

## A teleport onto an unknown tile is never "meant" (2026-10-24)

User: "Logan went into the Maus mini boss room and stood there while the custodian was killing him" (docs/NOTES.md
"Into the mini-boss room off the tank"). An off-the-tank tile was the room's door, and the Mover's rule "the walk's
goal tile teleported us, so it was meant" skipped the step back. Decisions:
- **Meant means known**: a teleport counts as meant only when the tile was a known teleporter before the step. The
  walks that go through teleporters on purpose step onto known ones or use `HuntLoop.teleport` directly, so they are
  unchanged. A walk through a known one ends there; replanning would aim back onto it from its far side.
- **No known way back: try the arrival tile.** Step off where it put us and back on. The user did that to get out:
  this room's arrival tile is its way back [INFERENCE: other rooms' doors may work the same]. Rejected: aborting into
  `HuntLoop.on_error`'s escape. The planner has no route out of a room it can't leave, so the escape would fail too
  and leave him there. The two steps cost nothing when they don't work.
- **The door's tiles are seeds** (`harness/data/teleporters.json`, the simulator's scenario), so no stand tile or
  route picks (5242, 612) again. Only the tile he stepped on is seeded: the other door-row tiles aren't proven.

## A pet out of a command's reach is walked to (2026-10-24)

A pet-lag abort on the walk in's first level ended in the escape home without GraveRobber (docs/NOTES.md "GraveRobber
left on the first level"). Three "All Follow Me" from 17 tiles never reached him. Decisions:
- **The runner goes within reach before it calls again** (`HuntLoop.fetch_pet`, from `wait_pets` away from a
  teleporter): a pet in view farther than `COMMAND_R` 12 gets the walk to within 8 of it first. Rejected: waiting out
  the pet's fight, the abort's 25 s made longer. A pet on guard keeps taking new attackers (a corpse eater after the
  apprentice), so the wait has no end it can see. Rejected too: a longer wait for the pets at the escape's gate. He
  came into view 2 s before the recall, 15 off and coming [INFERENCE: once his fight ended], which no fixed wait
  could count on.
- **The range is an inference**, 12 in the runner and 14 in the simulator, from 71 "All Follow Me" with a pet 7+ off in
  25 captures (in reach up to 13, out from 15). The simulator's pets used to hear every command at any distance, which
  hid the failure.

## An awake purger's shot is a wall for the patrol, unless it fights someone else (2026-10-24)

User: "Logan keeps getting stuck in a loop 'Out of the <mob>'s shot' when a purger or blightmare is fighting someone
else" (docs/NOTES.md "Cover loops at a purger or blightmare on someone else"), then, on the first fix's wait: "We can
just walk past when one is fighting someone else". Decisions:
- **Walk past one on someone else** (user). A purger or blightmare in war mode with another player or his pets beside
  it, and not on us or our pets, gets no cover, no stop at its shot's edge and no place in the waypoint filter
  (`Team.shy_threats`). Superseded: the first fix waited out of its shot (6b4f926, the 2026-10-21 rule "stay out of
  their aggro range"). In the simulator the walk past did no worse than the wait: 24 deaths in 98.2 h against 27 in
  98.7 h.
- **Every other awake one: wait out of the shot rather than pass through it.** The patrol picks only waypoints reachable
  without stepping into its shot, and holds when there are none (median 5 s, at most 32 s in the simulator).
- **The waypoint filter is a BFS on the crawl floor with the shot tiles blocked**, not a hard zone in the Mover. A
  hard zone would turn every waypoint beyond it into "no route" and rest it for `RESPAWN_S` (10 min) long after the
  monster is dead. Advances still stop at the shot's edge (`Team.pull_check`), so a waypoint chosen before it woke
  rests only 60 s.
- **A pet's war mode beside another player's fight isn't a fight of ours** (`pets_busy` via `Team.elsewhere`). A pet
  of ours swinging at it, or hurt beside it (`pet_attackers`), still is.

## Red players in the simulator (user 2026-10-22; built 2026-10-23)

User: red players as an option in the combat simulator, as close as we can get to the live reds, to model gold banked
per hour with them (reds are now the main or only killer) and to try strategies against them, e.g. hunting only where
we can loop round a red back to the gate; "reds are regular players so all the same rules apply to them, and extra
PvP restrictions apply to them once they attack someone". Decisions:
- **Evidence first, from both sides.** Two research passes before any code: every red encounter in the 31 live Logan
  captures (`sim/red_scan.py`, docs/NOTES.md "Red players met live") and the game rules from the knowledge base, wiki
  and patch notes ("Red players: the game rules"). Each scenario number carries its source; what the captures can't
  show is [INFERENCE] and set so the in-view numbers match.
- **Reds as players, not as monsters with a red name.** They come in the one way players reach Maus 3 (gold-gate
  recall on level 1 and the teleporters down; user 2026-10-23), find us by Tracking's real range and cadence, run
  on the map's walk graph across levels, follow us through teleporters but not the crimson gate (Heat of Battle),
  shove only at full stamina, cast with Magery's timings and its range-and-sight check at targeting (precasting), and
  hamstring, paralyze and stick explosion potions as the live ones did. Rejected: arrivals at the crimson gate (it is
  exit-only), a red that always knows where we are (Tracking gives a snapshot every 5 s within 50 tiles), the old
  single caster spec for everyone (live 30 mages, 7 melee, 2 archers of 39).
- **Our side's PvP rules in the world, not in the runner**: the 60 s PvP flag (the rope's 2-minute cooldown, no pets on
  the jump, PvP heal amounts), a red's hit breaking our recall, Hamstring's walk-only 3 s, Paralyze broken by damage.
- **Gold banked, not gold looted**, as the batch's money number: what reached home; a death loses what came after the
  last landing home, and `banked_per_h_net` charges each death the live 21 min downtime. Batches without reds report
  it too.
- **Validated two ways**: the encounter numbers against the live ones (rates, sizes, kits, attack timing, where we were
  met, the "red nearer the gate" lethality pattern), and the live red deaths replayed with the runner checkouts that
  died live (they die in the simulator too: 24/24, 8/12). Today's runner dies about half as often per encounter.
- Result: reds take 18 % of the banked gold an hour (25 % net of downtime); keeping the crawl out of the east wing
  changed nothing (deaths moved elsewhere). docs/SIM.md "Red players".

## Simulator casts and the Sim page (user 2026-10-23; built 2026-10-23)

User: see everything in a simulator run (mobiles, statuses, projectiles and casts, swings, damage) to devise new
strategies; the monsters' and pets' ranged attacks with their real cast and flight times. The user's choices and the
decisions built on them:

- **Record, then replay** (not a live sim feed): runs stay as fast as untraced (a 30-min run 1.5x the wall time), any
  run can be looked at later, scrubbing is free. `run.py --trace` and every `fight*.py --trace DIR`; the viewer is a new
  watch-only **Sim** page in the viz app. The trace is gzipped JSON lines (a key every 10 s, per-tick deltas, events):
  stdlib only, readable with `jq.exe`, a few hundred KB per run. The tracer only reads the world, so traced and untraced
  runs are the same run (tested).
- **Cast and flight times measured from the captures** (`sim/cast_scan.py`), not the wiki: the 0x6E -> 0xC0 -> '-N'
  gaps per type; types with no moving 0xC0 get their animation-to-hit time as the cast and no effect; types without
  samples the pooled default. The sim sends the live server's 0x6E/0xC0 to the runner.
- **Rules (user):** damage doesn't interrupt a monster's cast; a target out of range or sight at the release loses the
  cast; the phoenix gets the same model (its captures show no 0xC0, so its cast and flight stay 0). Rejected: a hit
  interrupting (as for players' spells, SPELL_INTERRUPTS.md): not what the monsters do live.
- **Unchanged on purpose:** the area specials (wail, burst, charge, transfix, feed) and the reds' own cast model.
- **No retuning to compensate**: the parity batch before and after is reported to the user, who decides (docs/SIM.md
  "Cast and flight times; traces").

## Tidal Tomb in the simulator (user 2026-10-24: "start building a dungeon crawler for Tidal Tomb like we have for Mausoleum")

Built from Logan Wolf's first visit (capture 20261024_102346; docs/SIM.md "Tidal Tomb", docs/NOTES.md "Tidal Tomb").

- **A second scenario, not a second simulator.** `World` was already scenario-driven, and `tidal_tomb.json` drives
  it like the Mausoleum's. The tools that assumed the Mausoleum now take the scenario:
  - `calibrate.py --scenario`, with its region and captures in the scenario's `calibration`.
  - `run.py --scenario` and `--area`.
  - `aggro` and `escape` read every scenario's `dungeon` and kb, so the live runner knows Tidal Tomb's recall
    rule and creatures too.
  - The Mausoleum's observations regenerate unchanged, apart from the new red count and rule text.
- **`inherit` instead of copies.** Logan's blocks (pets, pack, bard, vet, potions, the engine defaults) are the
  Mausoleum scenario's, updated key by key where Tidal Tomb measured its own: songs 16.9 %, durations, the Guide's
  barding chances. Rejected: copying ~10 KB of calibrated, annotated blocks that would drift apart.
- **The wiki rows come from a script** (`sim/kb_scan.py`), not a hand-made kb as the Mausoleum's was. The labelled
  Creatures List rows are unambiguous where a creature page's second table row isn't.
- **Level 1 is the default hunt.**
  - It is the recall arrival and has no oxygen.
  - Logan spent most of his time there.
  - The runner leaves by the golden moongate at the arrival to Totem. Its leave by recall casts where it stands,
    and the dungeon rule refuses that away from a gold gate.
  - Level 2 runs by arguments (docs/SIM.md). In the simulator they die far more often (3 of 8) and meet the oxygen,
    so a level 2 crawl needs its own rules first.
- **New mechanics only where the capture showed them and the wiki or patch notes named them:** oxygen (patch notes),
  the ooze aura (capture + the follower ability), the nest crab's broodlings (capture; [INFERENCE] as to the cause),
  the bottomfeeder's feed.
  - Seen but not modelled: armor pierce, large swing, poison and pressure. The evidence is 1-2 samples (and armor
    pierce's size is unmeasured; the player's armor itself came later, below).
  - No retuning toward the visit's numbers. Logan's damage taken on level 1, a quarter of live in the simulator,
    is left as a named gap (one visit, 8 minutes).

### Level 3 and the later visits (user 2026-10-25: "mine the session for new information to flesh out the simulator")

Docs: docs/SIM.md "Level 3 and the later visits", docs/NOTES.md "Tidal Tomb: the later visits and level 3".

- **Every visit is evidence, not only the one asked about.** Capture 20261024_102346 had four more visits after the
  first (2026-10-24 evening, level 3 included). They doubled level 3's minutes and gave the oxygen deprivation.
- **Measured numbers over the patch notes'.** The deprivation came at 732 s under, every 6 s, never below the floor
  (the notes: 10 minutes, every 5 s). Major at 1464 s is the one inference.
- **The later visits' spawns go in only where the first visit couldn't see.** Rejected: every first sighting. Monsters
  wander, so later sightings of a known spawn would add it twice.
- **One calibration over mixed pets.** `damage_to_pets` mixes the gravebug's and phoenix's armor with the Nightmares',
  GirthyEarthy's and the imp's. The level 3 numbers the specs use are overrides from the single hits, each with its
  victims in the note. Rejected for now: splitting calibrate by pet. The sim has only the gravebug and the phoenix to
  apply it to.
- **Level 3's attacks use the existing `burst`, with an `id` and a `targets` cap.** Rejected: a stacking scorch and
  the astral backlash as new mechanics. One fight each, and the backlash's effect is unmeasured.
- **The dolphin is a refill zone, not a mobile.** The runner reads no oxygen yet, so a visible dolphin would change
  nothing it does.
- **No retuning.** Level 3 in the sim kills Logan (5 of 8 runs, 4 on the walk in) where live he lived. Level 3
  also has no way out for the runner (the leave gate is a level up). Both are named gaps in docs/SIM.md.
- **Leaving level 3 by `--leave-via`** (user, same day: "We can leave lvl 3 by going back to the crimson gate on lvl
  2, as I did"). An argument naming the teleporters up, as `--enter-via` names the ones down; the leave walks them
  with the walk in's own legs, then the gate. Rejected: teaching the Mover to plan over teleporters. Every leave,
  flight and escape assumes a one-level route, and the walk in, its dashes and its escapes are already built on
  named teleporters.
  - A leave that went up a level isn't called off for being healed. Otherwise the hunt goes on on the wrong level
    (the simulator's first batch).
- **Barding by the instrument's quality** (user: "Barding depends on the quality of the instrument"). The captures
  name it: an eminently melodious sackbut (+15) on 2026-10-24, an exceedingly melodious psaltery (+20) on 2026-10-25.
  The scenario kept each one's measured numbers (`bard.instruments`) and picked the one played last
  (`bard.instrument`). Rejected then: a formula from the instrument bonus to every number, on one Guide pair.
  Superseded 2026-10-26 (below, "Mastery Chain links in the simulator").

## Mastery Chain links in the simulator (user 2026-10-26: "We need to have the links as an input, rather than fitting to Logan's success rate with them on. My goal is to try different combos of links")

Docs: docs/SIM.md "Mastery Chain links", NOTES.md "Barding from the effective skill".

- **The barding numbers from the effective barding skill E** (80 skill + the instrument's quality + the Effective
  Barding links), not fixed or fitted ones. The store had what the 2026-10-25 decision lacked: five E levels in the
  songs and 4,293 task-log answers across them. Songs, durations and the chance's slope all follow E (NOTES). So a link
  or an instrument changes them by rule, and the instrument blocks went.
- **Each type's difficulty fitted on live answers at their E** (`bard_scan.py` -> `bard_observed.json`, per skill),
  then the Guide's chance, then the skill's median. Rejected: the wiki difficulty with a fitted skill offset (the
  2026-10-13 S = 149). The fitted d don't track the wiki's (23-57 against 67-205).
- **Links as a spec of counts and materials** (`--links`), with the wiki's per-link values, the game's 10-a-type and
  30-a-chain limits, and Logan's chain as the gump's totals (`current`). Exceeding his 13 slots is allowed and named,
  so planning ahead is possible.
- **Only link types with a mechanic in the simulator.** Barding (Effective Barding, Durations, Break Ignore, Damage to
  Barded), the follower types, effective Veterinary, Logan's damage resistances and Healing Received, gold drop.
  Player-damage, boss, loot and resist-skill types are refused with the reason (`mastery_links.json` `unmodelled`).
  Where the wiki gives no stacking rule, the choice is marked [INFERENCE] in the type's `effect`.
- **The fitted factors stay put.** `k_dmg` and the reference pets are built with the chain they were measured under
  (`anchors.chain`: Follower Damage 16 %), so a run's chain moves the pets and never the calibration.
- **The baseline moved to Logan as he is now**: supremely melodious (E 123, since 2026-10-25 17:36) and Follower Damage
  18.5 % (since 2026-10-25 11:38). Batches before this change ran E 118 and 16 %.

## The player's armor in the simulator (user 2026-10-24; built 2026-10-24)

User: the simulator ignored Logan's armor; his suits differed between captures, and the aspect tier changes the AR
(133 in exceptional verite plate with Command tier 14). Docs: docs/SIM.md "Player armor".

- **The wiki's rule, checked against the captures before adoption.** The rule: a physical hit loses U(AR/300, AR/150)
  of the suit's total AR; the suit's AR is base x (1 + quality + material + aspect).
  - The AR formula gives the server's 88/130/133 exactly.
  - `sim/armor_scan.py` read Logan's hits at AR 86 against 130-148: the live drop was 0.67x (95 % 0.56-0.75), the
    rule's 0.59.
  - Rejected: an empirical curve. It was only the fallback if the rule failed.
- **Logan's melee is now wiki-referenced** (the plan's option a): the wiki melee x 1.27, fitted on 164 hits.
  - Rejected (option b): the gravebug-referenced number x a refit factor. That keeps Logan's damage tied to the
    pets' numbers, which carry their own armor and the Command tier.
  - Pets keep their measured numbers.
  - The aspect tier moves them only as a what-if (`--aspect-tier`), relative to tier 14. The data show no tier
    effect on them.
- **Specials, spells and ranged attacks keep their measured numbers with no armor.** The corpsebride's, the
  countess's and the calamity's numbers on Logan stayed flat across AR. The plan's guess that ranged attacks and
  charges are physical was dropped for that evidence.
- **No retuning.** Parity moved within noise (damage on us 1,763/h against 1,795; live 2,265). Tidal Tomb level 1
  rose 442 → 520/h, so the threat fix the plan tied to a further drop was not needed for this change; that gap stays
  open.
- **Not modelled:** AR changes inside a run (the aspect lost on death, the +15 buff, pierce). The World has no
  resurrection, and pierce's size is unmeasured.

## Any pets on the hunt (user 2026-10-24: "so I can bring whatever pets I want")

- **The roster is the tamed pets beside Logan at the start, plus the remembered ones in view** (`tamer.resolve`).
  An untagged mobile there that may be a pet is single-clicked first, so its "(tame)" / "(bonded)" line comes
  (docs/NOTES.md "Other pets than GraveRobber and AlaFlambe"). `--pet` still overrides.
- Rejected: taking every notoriety-2 non-human within 3 tiles without the tag. A guild mate's pet or a
  summon would join the roster, and the runner would then wait for it or leave over it.
- Rejected: the stock right-click "Release" check (`mount.is_own`). It opens a context menu in the user's client
  for each pet. The tag plus "within 3 tiles at the start" is enough at the house.
- **The simulator's pets: done 2026-10-25** ("Logan's pets, teams and the Healer's Codex in the simulator" below).
  Until then it modelled the gravebug and the phoenix only.

## Pets standing locked off a fight get 'All Follow Me', not another walk back (2026-10-24)

Live 21:40:56-21:41:30: after "All Guard Me" the pets stayed locked on a reanimated daemon on another player's beetle,
out of their reach. The patrol, the reposition out of its reach and the walk back went round seven times (docs/NOTES.md
"Pets locked on a fight they can't reach"). Decisions:
- **`Team.rejoin` calls them off.** It says "All Follow Me" instead of walking back when the pet it walks back to is
  in view within `COMMAND_R`, on the tile it stood on at the last walk back within 30 s, and `pets_busy` is None. Then
  it doesn't walk back for 10 s, and "All Guard Me" comes once they're with us (or after the 10 s, or at once with
  something on us). `COMMAND_R` moved from loop_hunt to tamer, which can't import loop_hunt.
- Why the second walk back and not the first: a pet that moved since the last walk back is coming or fighting.
  Calling it off on the first lag would cut fights `pets_busy` can't see. Two walks back on one tile is the cheapest
  proof we have that it isn't moving.
- Why "All Guard Me" waits: [INFERENCE] a guarding pet may take the same unreachable target again. Following pets
  come along, and an attacker on us brings the guard at once.
- **Rejected: no reposition out of the reach of a caster on someone else's side** (`Team.elsewhere`, as `shy_threats`
  does for purgers and blightmares). The same 120 simulator runs with it: deaths 30 against 28 and 108.9k against
  112.8k gp banked an hour. It saves one reposition in the test and nothing in the batch.

## No walk to a hurt pet from out of a caster's sight into its reach; a full-health refusal is no drink (2026-10-25)

Live 2026-10-25 00:38:45 Logan died 4 tiles from an awake master reanimator: the reposition had taken him out of its
sight, and 3 s later the walk to a hurt pet (the tank at 92 %) took him back; its and a shadowguard's spells landed on
all of us, sight or not, and a potion click answered "You are already at full health." had started the potion clock
(docs/NOTES.md "Dead vetting a pet in two casters' reach"). Decisions:
- **`Team.to_pet` holds the walk** when an awake caster or ranged monster (not a shy one: its own shot rule and cover)
  doesn't see us here and the vet tile is nearer it within its reach, by distance, for a pet at `TO_PET_CASTER_AT`
  (50 %) or more. Below that, or a ghost, the walk goes: the pet's life comes first then (heal in place, the pets guard).
- Why distance and not sight for the tile: every hit after the walk landed on a tile our map says neither caster sees.
  Why only from out of its sight: a walk from inside its sight doesn't add its single-target spells, and the rule for
  every caster cost pet deaths (0.62 an hour against 0.43 in 240 simulated runs) for fewer deaths (0.192 against 0.257).
  The kept rule: deaths 0.245 -> 0.203 an hour, pet deaths 0.44 -> 0.46, gold even (360 runs a side).
- **`HuntLoop.drink`: cliloc 1049547 is no drink**: the clock stays (ServUO takes the potion lock only on a drink; a
  wrong one is set right by the server's "You must wait" refusal anyway).
- **Not done**: the simulator's casters still cast one target at a time; `ranged.area_r` (a spell on all of us within
  reach, sight or not) is in `sim/fight0038.py` only, its radius [INFERENCE] from one fight. The scenario's caster
  types would need it measured over the captures before batches use it.

## Less time not fighting (user 2026-10-24: gold an hour +10 %, deaths per gold no worse)

Simulator goal on the efficient-crawl branch (docs/NOTES.md "Less time not fighting in the simulator"). Kept: no
spacing once a fight is over; out of a fight the vet supplies and the wait before moving on only for a pet below
`tamer.CALM_HEAL_AT` 0.80; waypoints in sight within 7 count as visited and end a patrol leg; `HOLD_S` 5; the blink
wait ends when the monster is back; the eerie corpse walk bounded like a loot walk; no seek toward a monster the fight
passed over; no "All Kill" at a monster another player already has below 80 % of its hits (its corpse is theirs: the
user's 2026-10-17 "all kill if it's aggro'd on something else" narrowed). Gold an hour +5.5 % / +5.9 % on the two seed
blocks, deaths per gold 0.88x / 0.68x, short of the +10 %: with the other players on, the level is supply-bound, and
no further runner knob screened moved it beyond the screens' ±2 % noise. Rejected: routing the patrol round idle
purgers' and blightmares' sight (more kills, less gold); opening with "All Kill". *Live*: untried; the §5 command is
unchanged.

## A purger backed off from gets the pets patrolled at it (2026-10-25)

User 2026-10-25: the runner says "the pets first in the corpse purger's sight", then doesn't patrol the pets in and
walks away (docs/NOTES.md "Backed off from a purger, never patrolled at"). Decisions:
- **`tamer.PATROL_SPOT_R` 2 -> 3**, the live demo's spot (3 from the purger, Logan 14 from it), so the back-off's
  14 tiles, out of an awake purger's and blightmare's reach, still has a spot within `PATROL_MAX_R` 11.
- **`Team.back_off` prefers, out of sight, a tile with a patrol spot in reach**; else as before, the farthest.
- **`Team.patrol_pull` ignores `HuntLoop.skip`**: the fight's pass-overs are about fighting or approaching it; a
  refused or timed-out patrol has its own `patrol_failed`. Why none goes out is logged now.
- Simulator, 144 runs a side: deaths an hour 0.22 -> 0.11, gold banked an hour +4.7 %, purgers killed +16 %,
  patrols 247 -> 497, the shy monsters' damage on Logan per shy kill unchanged; pet deaths 4 -> 7.
- **Rejected: keep radius 2 and back off to 12-13.** Fewer deaths too (0.09) but 17 % more shy damage per kill: an
  awake blightmare reaches 12 and a purger 13. **Rejected: `PATROL_MAX_R` 12**: no click past 11 seen live.
  *Live*: untried; the §5 command is unchanged.

## A monster the pets were sent at isn't passed over on its way down (2026-10-25)

The Seer: "All Kill" at a monster on another player, then "passing over … its loot is theirs" 1-5 s later (docs/NOTES.md
"'All Kill' then 'passing over' a few seconds later"). Decision: `THEIRS_BELOW` decides before the pets are sent, not
while they run there: no pass-over within `KILL_REPEAT_S` of our "All Kill" at it. Live 11 of 21 such dropped corpses
were ours anyway; the simulator is neutral (200 runs a side on two blocks: gold +0.4 %, deaths 20 -> 17). Rejected: a
higher `THEIRS_BELOW` or a damage-rate rule; the hits read was right at the "All Kill", and the pets' share of the drop
can't be told from the other player's. *Live*: from the next `ctl run`; the §5 command is unchanged.

## No recall in without an instrument or the charges home (2026-10-25)

Logan's only instrument wore out mid-run and the visit went on with no bard skill, area peace in a jam included
(docs/NOTES.md "The instrument wore out mid-run"); later the rune tome ran dry at home ("The rune tome ran out of
charges at home"). Decisions: the runner doesn't recall in from home without an instrument while any bard skill or
song is on, nor without a charge for every recall of the visit (in and home; 2 on §5's one tome) when Recall can't
be cast (`low_supplies`, the run ends at home, as the miner does without a pickaxe); the `--no-*` bard flags go
without an instrument. An instrument is known by graphic or by name: graphics alone missed both the psaltery and the
user's bamboo flute. The book is glanced at before each recall in (one gump, as `act recall --check`): the recall
results' charge counts aren't kept across runs, and the first run's in needs it too. Rejected: fetching a spare
instrument or recall scrolls (none known in the house; the user supplies them). A mid-visit break doesn't end the
visit: the pets fight on, and the leave rules are unchanged.

## Bard skills at a caster or shy monster the pets fight: a peek from cover (2026-10-25)

User: no Discordance or Peacemaking at a blightmare or a reanimated daemon the pets fought, "a huge issue" (docs/NOTES.md
"No bard skill at a caster or shy monster the pets fought"). The cover and the caster reposition keep Logan out of such
a monster's sight, and the skills need sight. Decision: for a target that isn't on us, the bard step leaves its own
sight of us out of the score and goes for no more other eyes than here, and the skill goes at once from that tile;
the care then walks back out. Its shots stay on the pet it fights (the simulator's stray share at the player in its
sight, 0.2 as live, covers the rest). Rejected: casting from cover (the server refuses out of sight), and dropping
the cover/reposition while a skill is due (the whole fight in its sight, not a second or two). Simulator 98 runs a
side: deaths 11 -> 6, banked/h +2.7 %, discord/h +9 %, peace/h +18 %. Same incident, the vet: a pet below 80 % (was
50 %) is walked to inside an awake caster's reach from out of its sight (user: GraveRobber "~70%" unvetted for a
while); the 00:38 death's tank was at 92 %, still held; the simulator shows no change in deaths.

## The patrol's seek by gold a kill over the time it costs (2026-10-25)

User: the patrol walked away from a fresh reanimated daemon at 22:02 (docs/NOTES.md "The patrol walked away from a
fresh reanimated daemon"). The seek ranked monsters in view by gold a fighting minute, which puts a slow, rich kill
below a quick, poor one. Decision: gold a kill over the fight plus `SEEK_KILL_S` 20 s (what a kill costs beyond the
fight live) plus the walk. Rejected for now: "All Patrol" at a monster the fight passes over for not seeing us
(simulator: -3 % gold, more deaths); a memory of idle monsters out of view (not needed for this scene; open if the
walk-aways go on).

## No recall in without the armor aspect; a wider peek for bard skills (2026-10-25)

User: "make the runner check aspect before a trip and refuse to leave home without it" (the essence ran out mid-run at
21:16 and two hours of runs went unaspected). Decision: the Aspect Mastery menu read at home before each recall in
(one gump, as the lumber runner's), essence left and a worn armor piece in the armor aspect's hue, else the run ends
at home with a `low_supplies` juncture; activating it stays the user's (essence and the choice of aspect). Rejected:
activating it ourselves as the lumber runner does (the user asked for a refusal; Logan's aspect and hues are his), and
judging by the "(Command Aspect … xp)" lines (they went on after the revert).
User: "lots of mobs are fighting the pets and we are not barding any of them ... Should be firing off discos and
provos." Decision: the peek at a target not on us goes up to 4 steps, counts only eyes that would wake to us, and
ignores vet range unless a channel runs. Simulator: discord +7.5 %, peace +9 %, gold even, damage on Logan +13 %
(the purgers' stray shots): the user's priority on the bard skills taken over that cost.
After the 00:32:45 death (docs/NOTES.md "Dead in a daemon's and a blightmare's reach": vet walks for GraveRobber at
65-75 % into a reanimated daemon's area-spell reach, no clear tile within 6 steps), asked whether to go back to the 50 %
hold near casters (all of them, or the area casters only) or keep 80 %: the user kept 80 % (2026-10-26). The simulator
didn't reproduce the death at either threshold.

## The pets first on the casters, as the user plays them (2026-10-26)

Three deaths on 10-25/26 under casters, one under a purger while the user played; the user fought the reanimator's room
with no damage by sending the pets in first ("I keep the pets between me and mobs so mobs aggro on the pets, I don't walk
over to other mobs while our pets are fighting"). The captures: the difference is who the monsters target, not how far
Logan stands (docs/NOTES.md "How the user plays the casters and purgers"). Decision (the user picked all five): the
casters that stand and cast are shy like the purger (back off from their first sight, "All Patrol" at them, cover from
their shots), the purger's barrage and the reanimator's chain lightning get area zones round them and the pet beside
them, a purger's and a blightmare's spawn spots join the backspawn rule, and no loot walk goes into an idle shy monster's
first sight. Rejected: treating the reanimator's walls as see-through (the user tested: they hold; its out-of-sight
hits were spells begun while it saw Logan); the other shy casters' spawn spots (the simulator: -8 % gold, more deaths).
Accepted cost: about 5 % gold in the simulator for 12 % less damage on Logan and fewer deaths there; the simulator
can't reproduce the live caster deaths, so the live runs decide. Next on the list: vet "in passing" (begin out of
reach, step in for the finish; the user's trick), with the stale "never mid-channel" gates.

## Shy by the wiki AI; Tidal Tomb's level 2 fought through to level 3 (2026-10-26)

User: Tidal Tomb's drowned mariners, soulcatchers and zoas are "conceptually similar to the ranger attackers and
casters in Mausoleum 3" (agreed: treat them the same); "in TT we'll always have to fight in 2 in order to get to 3".
Decisions:

- **`aggro.shy` from the wiki AI, not a name list.** Mage or ranged AI from difficulty 60 up, plus the shadowguard (a
  meleemage the user named). The floor leaves out the weak casters: a familiar is 21 and a reanimator zealot 50.
  Rejected: adding the Tidal names to the list, a second list to keep in step with every new dungeon.
  `SHOOTERS`/`RESPAWN_SHY` stay the purger and the blightmare: the backspawn rule cost gold for other types before.
- **`--fight-via`** names the walk-in legs fought as on the hunt level. It uses the tick's own fighting, split out as
  `combat_step`, rather than a fight-mode walk of its own. It is an argument, not automatic for every leg: Maus 2 is
  run through. Lagging pets get the crawl's rejoin, not `wait_pets`, whose 'All Follow Me' pulls them off a fight and
  aborts.
- **Accepted:** simulator level 3 runs with a death 15 of 16 -> 9 of 16, gold an hour 99.6k -> 158.9k; Maus 3
  unchanged; level 2 crawl deaths 7 -> 3 of 16 for 10 % fewer kills. The fought leg takes 11-20 min. **Open:** level
  2's melee killers by the leave gate (ocean bedrock, ironscale grouper) on the leg and on the leave (docs/SIM.md
  "Level 2's shooters").

## Level 2's bedrocks: out of the spray, provocation first, the fought leave (2026-10-26)

User: "Implement your next steps. I stand behind the ocean bedrock so it doesn't do its spray attack on me. In that
area I'm generally able to keep the mobs provo'd onto each other." The captures were read first (docs/NOTES.md "The
ocean bedrock's spray"). Decisions:

- **The spray's cone is an area zone, not a new stance rule.** Reusing the stand-off's zone puts every chooser out of
  it (stand-off, vet tile, loot tile, bard step). The cone's tiles are in it at 1 each, along the bedrock's direction
  and toward each pet beside it. Its action 18 starts the stand-off at once. "Behind" in the data meant off the line
  from the bedrock to the tank, not its back: the cone covers exactly that line.
- **Provocation before Discordance whenever two monsters are in the fight** (`PROVOKE_FIRST` "pairs"), not only for
  a bedrock ("sprayers"). The A/B in the simulator: "sprayers" left level 3's deaths at 12 of 24, "pairs" brought them
  to 5, and Maus 3 halved (8 -> 4 of 96).
- **No loot walk to within 2 of an awake monster** (the seed 6 death).
- **The planned leave from level 3 fights across level 2** whenever `--fight-via` is given. Fought legs don't count
  toward the hard timeout; `--timeout` itself still starts the leave.
- **Rejected:** a separate "behind it" tile chooser, which the zone makes redundant. Also rejected: lowering the fish's
  melee for their spins, which needs a capture that tells spin hits from swings.

## Tidal Tomb level 3's new ground, Backlash, the barding fit (2026-10-27)

User: mine the new runs; "maybe we should just avoid [the giant astral zoa] when our pet team relies on caster(s)";
does the simulator model provocation right, and discord first or provoke first? Decisions:

- **The barding model follows the live fit.**
  - A Provocation's d is the pair's mean Discordance d plus 31.5.
  - A failed Provocation draws nothing.
  - A Discordance gets no +25 chance on a barded creature.
  - `provoke_x` 7; break rates by effect; Tidal Tomb's own d from the hand play.
  - Rejected: keeping the per-type provoke fits. bard_scan's barded rule inflated them, and they made the sim fail
    provocations too often.
- **Provoke first stays the default** under the corrected model. Maus 3: deaths 10 -> 7 of 96, damage -12 %, the same
  kills and gold. Level 2: damage -24 %. The user's worry is real but small: 78 % of the monster time on us
  undiscorded against 75 %. The cooldowns don't differ: the skill timer is 5 s for every skill. `PROVOKE_FIRST`
  "off" keeps discord first available.
- **`--avoid-backlash` on by default:** the giant astral zoa joins the never-aggro set when every attack pet casts.
  The pets come from the character's sim tamer file, which the runner reads for this. Rejected: a live Backlash-stack
  rule (break off at a fizzle chance of 0.5), which would need the pets' buff timers read live; the never-aggro set
  already exists.
- **Pressure, Backlash and poison are generic sim mechanics** (`pressure`, `debuff`, `center: "target"`), not
  per-creature code.
- **The level 3 route stays the old one** (6884, 1929) in the docs' command. The west entrance's crawl died 17 times in
  24 against 8.

## The house's repair bench before each recall in (2026-10-27)

User: "I installed a repair bench in Logan's house. The combat runner should now double click the repair bench before
each trip into a dungeon to repair our equipment (instrument and armor)." Decision: `HuntLoop.repair_gear`, first of
the pre-trip checks in `recall_in` (so every trip, the first one from home included), at home only, one double-click
(`repair.py`, the bench in homes.json `repair_bench`); `--repair` on by default. An empty bench ends the run at home
with a `low_supplies` juncture, like the other pre-trip checks: recharging it (repair kits) is the user's, and going in
unrepaired risks the instrument (lost at 0 uses; live 2026-10-25 the only one broke mid-run). Rejected: repairing
in `home_landed` after the drop-off (misses the first trip of a run started at home); reading the bench's charges
each trip (a second click for a number the "out of charges" line already covers); a `ctl act repair` (not asked).

## Logan's pets, teams and the Healer's Codex in the simulator (user 2026-10-25; built 2026-10-25)

The user: every pet Logan uses replicated (stats, attacks, abilities, bestiary perks) as live; a choice of team and
Taming Bestiary loadout in `run.py` and on the viz Sim page; the Healer's Codex as live, set as his. Built as
`sim/tamer_scan.py` (his gumps from the memory store), `data/sim/tamer/logan_wolf.json` (the hand-authored data) and
`sim/pets.py` (the mechanics); docs/SIM.md "Pets, teams and perks". Decisions:

- **Mechanics plus a live anchor** (the user's pick): the Guide's base stats through the wiki's level table, the
  wiki's and the gumps' formulas for every stat, ability and perk, the absolute numbers pinned to the captures:
  each pet's live attack period (`k_speed`), one damage factor for every pet from the gravebug's live median melee
  number (`k_dmg` 1.559: the unknown stacking is the tamer's, not the species'). Rejected: a calibrated spec per pet
  (most pets have no attributed numbers; perks would then change nothing).
- **The monsters' numbers stand for a reference pet** (`defaults.pet_ref`, an entry's `ref`) and each pet takes them
  through its own armor, resist and resistance over the reference's: the scenarios' measured numbers stay as they
  are, and a pet with another loadout takes more or less. The old `melee_taken_x` 1.7 (phoenix) comes out of the
  armor rule (1.83 in the model), `pet_taken_x` out of the Command tier in the build.
- **The default team stays GraveRobber and AlaFlambe with Phoenix/Gravebug** (the user), so batches stay comparable.
  Bonus pools add inside and multiply across [INFERENCE].
- **The plan's contingency taken, then replaced**: with the shared factor the default batch made 42 % more kills an hour
  (130.3 -> 184.9); AlaFlambe got a stopgap factor (its model mean per number = the old spec's 438, which was a
  phoenix *attack's* damage) at +28 % (166.8). Replaced the same evening by the recalibration below: 144.7 (+11 %).
- **The vet heal is the wiki's formula** with the codex (Specialist + Without Borders), not the gravebug's bar x an
  assumed 450 hits; Lore counts 0.2 a point (#11327; the plan's numbers had 0.3). `vet.res_hits` 0.31 -> 0.10: the one
  live res was the gravebug's with Critical Care rank 2 (+21 %), which the model now adds per pet.
- **run.py hands the runner `--vet-range`** from the codex (5) unless given; before, the runner's flag set the
  simulated reach.
- **The Sim page refuses a bestiary file path** (it would read arbitrary files); `run.py` takes one.
- Open besides the kills: spells phoenix : gravebug 0.86 against the live 0.79, and the area hits 1.09 against the
  live 37 vs 37 (the gravebug's Tank-tab resistance).
- **Phoenix/Gravebug's Utility tab is empty** (the user, 2026-10-25: the team has no utility-class pet): recorded as
  known (`bestiary_known`), so the mantis runs without perks and without a warning. A tab the scan decodes later wins.
- **The anchors and reference pets are pinned to the state they were measured in** (2026-10-25 evening, after the
  user's "The pets are at level 15 now" and his reset of Phoenix/Gravebug's Attack tab at 21:01): `anchors.loadouts`
  (the loadout as it was, read with `tamer_scan.py --as-of`) and each anchor's `level`. Before, `k_dmg`, AlaFlambe's
  factor, `k_speed` and the references were computed with the current scan: a rescan would have folded every loadout
  or level change into the factors (AlaFlambe 1.221 -> 1.695 for the Attack tab reset, its damage unchanged). Rejected:
  a per-tab history in the scan's output read by date. The copies with their evidence are explicit and few, and
  `--as-of` reproduces them.
- **A pet's aggro draw follows its Aggro setting, not its class** (2026-10-28, the user: "I switched the aggro setting
  on AlaFlambe to on, so it is the tank now"): `aggro_threat_x` on 4.0 / off 0.1 (the fitted gravebug and phoenix
  values) from the Guide's Abilities page (`tamer_scan.py` reads it; never viewed: on for a tank-class species).
  `class_threat_x` is gone. A team preset pins a setting it played with (`teams.*.settings`: phoenix_gravebug's
  AlaFlambe off and ranged, as 10-12 to 10-25), so the calibration batches keep their team. The Nightmares had aggro
  on in every view, so the nightmare presets now draw as tanks where they drew 0.1. A caster on melee Combat Distance
  casts from range 1 [INFERENCE]. Rejected: an `aggro` flag hand-written per pet (the store has every toggle).
- **Recalibration is against live play, per team** (docs/SIM.md "Recalibrating"), done 2026-10-25 evening:
  - `k_dmg` keeps the gravebug's live median as its anchor but takes the barding out of it: all 198 hits were under the
    Song of Provocation, 91 on a discorded target, and the World applies both again (1.559 -> 1.2425).
  - AlaFlambe gets its own fitted factor (k_dmg x 0.73) from the 83 GraveRobber + AlaFlambe visits at level 15 (kill
    times 0.76 -> 0.98 of live), agreeing with its live damage per attack (530 against the model's 721). Rejected:
    removing Spellsurge (only 18 % of the gap, and the wiki says it works) or a factor for every caster (the
    Nightmare/Fire casters, held out, land at 1.06 of their live kill times on the shared factor alone).
  - Spellburn and Epic Barrage are physical against half the armor, as the patch notes word them.
  - `parity.py --team --until` and `run.py --pet-level` let each team's window be compared as live played it.
  - Open: the Random team (2 h, levels guessed) at 0.77; kills an hour ~1.3x live in every team, the time between
    fights, not the pets.
- **The 21:39 Phoenix/Gravebug loadout is tested live, not fitted** (2026-10-25 23:06): the model made it 10 % faster
  than the 10-15 one, live it was ~19 % slower per monster on 4 visits. A Behemoth hypothesis (its damage not reaching
  AlaFlambe) led to a live A/B; the user then found the cause: Logan's armor aspect was off (out of arcane essence,
  21:16-23:17), and `run.py --aspect-tier off` reproduces those visits (kill time 0.93, 0.75-1.56). The hypothesis is
  withdrawn, `k_x` and Behemoth's rule stand. The loadout's test continues with the aspect on, from 23:18.
- **A live window is compared only with Logan's aspect state known** (his AR in 0x11): an unaspected stretch moves the
  pets' damage, speed and resistance by a quarter or more. Open: a per-visit aspect flag in parity.py's live side
  (proposed, not built); the scenario's suit (AR 133) predates the 122 one worn since 10-24 22:08.
- **The new Tank tab's resistance isn't fitted on one hour** (2026-10-26): live, Stalwart 3 and Survival 3 cut
  GraveRobber's numbers 6 % (2-10 %) where their texts and the model give 17-25 %. A factor of ~0.25 on the bestiary's
  resistance would match it but moves the codex's and every tab's too, without a mechanism; the stacking with the
  Command aspect's resistance (additive in the model, [INFERENCE] in the pools rule) is the other candidate. Next: more
  aspected hours with this loadout, then fit one with the Random team's GirthyEarthy (1.54x the model's damage) held out.
- **Dreamlull and the hex/hinder perks follow the texts; live gives no proc to fit on** (2026-10-28, Bussin the
  incubus, docs/SIM.md "Dreamlull", docs/NOTES.md "Dreamlull live"): a proc (15 % x Primal's) hexes the target (its
  magic resist off 25 a slot x Scour for 15 s, for every caster: wiki StatusEffects and Arcane Staff) and hinders it
  (1 s a slot: no step, swing, cast), both cut on difficulty 100+ (#5947). Opportunity (+100 % within 10 s of the pet's
  hinder), Punishment (DamageMax x 0.5/1.5/2.5 when its hex ends) and Scour (x2 on the hex) take their gump rules for a
  species with the mechanic; the flat 5/15/25 % stays for one without. Rejected: fitting the rate or the pause on the
  captures: Dreamlull leaves no packet (no text, effect, buff or health-bar flag; 832 casts searched) and the monsters'
  swing and step timing around Bussin's landings is too thin to see a 1 s root (11 of 37 against the phoenix's 4 of 21,
  p 0.28). Open: Bussin's own numbers against the model's (his and the phoenix's numbers interleave on one target).

## Shift skills take the user's instructions; a hunt level per shift (user 2026-10-27)

User: "Make a skill like the Mausoleum skill but for Tidal Tomb. The new skill and the Maus skill should let me add
instructions like 'only level 1'."

- **Two skills, one Seer procedure.** `.omp/skills/tidal-tomb-shift` is the Mausoleum skill's loop with Tidal Tomb's
  rune check, levels and simulator; both send the Seer docs/OVERSEER.md §5 "Mausoleum crawl shifts". Rejected: a
  second Seer procedure for Tidal Tomb, which would drift from the Mausoleum's.
- **Instructions map to a command, a flag or a shift rule** in the skill (its section 0), before dispatch. The level
  commands live once, in §5 "Another hunt level by the user's word", and the skills point there. The command replaces
  §5's for every run, the restart from inside and the go-home run. One that maps to nothing goes back to the user as
  runner work.
- **A level is the runner's arguments, not a new flag.** The crawl already keeps to the floor it reached (teleporters
  are walls), so a level is where `--enter-rune`/`--enter-via` leave Logan and which gate `--leave-gate`/`--leave-via`
  name. Maus 1: no `--enter-via`, the gold gate beside the arrival (simulator 2026-10-27: 3 of 3 home, no death; its
  menu's Totem row [INFERENCE]). Maus 2 has no way out of its own, so it is refused until the runner gets one.
- **Tidal Tomb's level 3 needs the user's go** after the skill states the evidence (never live by the runner,
  simulator deaths 5 of 24), rather than being refused: the user plays it by hand and asks. Level 2 needed one too
  until the user dropped it (2026-10-29: "the runner actually has done it live before"); it runs on the word "level 2".

## Overweight: drop gold; an ally's fight and kill left alone (user 2026-10-28)

User, after Logan stood at 462/450 on a Tidal Tomb leave: "1) Have it drop gold when its overweight 2) It should
detect when a player already killing a mob is an ally (member of DTF or an allied guild, a 'green' player) and not
attack that mob. Goal is to avoid 'kill stealing' allies or looting their kills. Even if we don't damage a mob an ally
kills, we can loot the corpse, but we shouldn't as that is rude." Decisions (docs/HUNT_LOOP.md "Someone else's fight",
"Kills", "Loot rights"):
- *Gold, dropped in the walk*: the Mover detects the overload (state weight over `weight_max`, or the server's line)
  and calls the host's `unload` (the hunt runner's `drop_gold`) before stepping, so every walk (the leave, the escape,
  a loot walk) gets free the same way. Down to `weight_max - 1`, the fewest coins: dropping to the loot margin would
  throw away ~1,000 gp more, and the loot guard (fixed the same day) stops further loot anyway. Rejected: dropping
  items (a stack of gold is the bulk: 40k gold is ~400 of Logan's 450 stones), and stopping the run (Logan stuck
  in the dungeon is the failure the user saw).
- *Ally by notoriety 2 or our guild tag* (`threats.ally`), not `threats.friendly`'s faction-mate rule: the user named
  guild and alliance. Who fights a monster is read as `Team.elsewhere` reads anyone's fight (in war mode within 2), so
  the two rules agree; a monster that attacks us or our pets is still fought (the pets defend anyway), but its corpse
  isn't looted if an ally was seen on it. Rejected: reading loot rights from the corpse's notoriety (an ally's kill can
  show grey to us: the user says we can loot it).

## A clearwater grouper's bubbles are fire for their life (user 2026-10-28)

User, live on Tidal Tomb level 2: "Standing in the clearwater group's exploding bubbles is no good". The captures
(docs/NOTES.md "Tidal Tomb: the clearwater grouper's bubbles") show a delayed hit on tiles. Each bubble is a ground
item that lands on a tile up to ~5 from the grouper and pops 1.9 s later on everyone within 1 of it. Decisions
(docs/HUNT_LOOP.md "Fire and bubbles on the ground"):
- *The fire model*, not an area hitter's. The hit is centred on each bubble's tile, which shows 1.9 s ahead. So the
  reach of a bubble on the ground (`Team.bubble_tiles`) joins `fire_tiles` for its life, and every chooser and route
  that keeps off fire keeps off it.
  - Rejected: an `AREA_R` round the grouper. Its bubbles land up to 5 off at random, so a stand-off would keep Logan 6+
    from every awake clearwater grouper and out of `--vet-range` 5 of the tank beside it.
  - Rejected: the corpse purger's target-centred barrage. The bubbles don't follow the grouper's target.
- *Out at once*: `Team.out_of_bubbles` runs first in `stand_off`, mid-channel too, unlike fire's step. The fire
  hasn't been seen to hurt; a pop costs Logan 31-45, and a step only shortens a channel's reach to 1. It also runs
  between a loot's lifts, since the live hits came while Logan looted.
- *The bubble's own item, not its throw*. The 0xDD8F moving effect is also the ocean bedrock's spray wave, and the
  runtime drops effects more than 2 tiles from us. The item (0xDD8F-0xDDAE with hue 0x0B40) comes to the world model
  whole.
- *Simulator*: the grouper's `bubbles` special, with the volley, the reach, the per-volley single hit and the
  numbers as measured. The before/after batch is in docs/SIM.md "The clearwater grouper's bubbles".

## Out of the ocean bedrock's cone, whatever the walk (user 2026-10-28)

User, live on level 2: "Logan is still standing directly in ocean bedrock spray. Figure out why". The cone was already
a zone for the choosers (2026-10-26); the three live hits were walks through it (docs/NOTES.md "Tidal Tomb: Logan in the
bedrock's spray"). Decisions:

- *Routes, not more choosers*. The cone goes among the Mover's `avoid` tiles (a new flat-cost set beside
  `danger_tiles`), updated each tick and on every step, so every walk bends round it and crosses it only when there is
  no other way. Per-tile `danger` entries as for fire would put 80-240 entries in every route's cost loop.
- *No replan request on a cone change*: the bedrock turns with its target, and danger replans count toward "boxed
  in". A walk already under way stops at a wind-up instead (`spray_stop`, in or a step from the cone), and no walk to a
  pet, off the tank, of the patrol or to a corpse starts while a wave is due beside us (`spray_wait`). Not a tick-wide
  wait: with one, 48 simulator runs had pet deaths 3 -> 9 and banked an hour 123k -> 95k [INFERENCE: it held the vet
  supplies and the bard skills too, 1.6 s a wave].
- *The fewest steps on a wind-up* (the wave is 0.71-1.41 s off), even beyond the vet supplies' reach; the walk back
  to the pets comes after the wave. Without a wind-up the stand-off keeps its ranking, with tiles down a cone from a
  step of the bedrock's ranked below the ones beside or behind it (`SPRAY_AHEAD`).
- *An awake bedrock coming on holds the advance* as an idle one that sees us does: it walks at what it fights, facing
  it, so the patrol toward it walked down its cone.
- *The loot waits* when every tile within 2 of the corpse that we reach lies in a cone: the corpse stays queued.
- *Ties go to the lowest tile again* (2026-10-29; the user: keep the gain, bring the pet cost down). fbf76ab dropped the
  tile from the stand-off's ranking in every stand-off, not only a bedrock's: the walk order decided ties. Level 2 in
  the simulator, 768 runs: pet deaths 175 against the baseline's 153, back to 123 with the old tie-break, and Logan's
  spray hits 575 -> 472 against the baseline's 952 (docs/SIM.md "Tidal Tomb: Logan in the bedrock's spray"). The rest
  of fbf76ab stays: turning off the avoid, the hold or the stop/wait left pet deaths as they were; turning off the
  fewest steps or `SPRAY_AHEAD` lowered them less and gave back spray hits. The wind-up's fewest-steps pick keeps the
  walk order: with the tile there too, 143 pet deaths and 513 spray hits. The per-pet-death readout (`World.die`) stays
  in the simulator's summaries.

## Dead on Tidal Tomb level 1 on the way out (live 2026-10-28 22:53)

A `stop --after-trip` started a planned walk out while a lowtide scavenger came at Logan; he died beside it and a
dungeon-nest crab (docs/NOTES.md "Tidal Tomb: dead on level 1 on the way out"). Decisions:

- *A planned walk out stops for what comes, as the hunt holds for it* (`HuntLoop.coming`, `COMING_R` 10 = `HOLD_R`,
  `COMING_S` 5 s = `HOLD_S`), and stands with the pets on guard (`walk_in_fight`) until it stops coming. Rejected: a
  route round it (it comes at us whatever the route) and a fought leave on level 1 (the user's "all follow me to run
  out"; the stand is only for what comes).
- *The walk's guard stays out of the stand* (`walk_fighting`): one order in a fight, `walk_in_fight`'s, where the
  stand-off and the retreat come before the vet supplies on a pet. Not a "self before a pet" rule: the channel is the
  same either way.
- *No area peace on a planned walk out* (`walk_out_fought`): it buys 2 s to walk on, and that walk stops for what
  attacks it anyway. Kept for hurt leaves and flights (fight1949).
- *A guard that acted means a fresh read* (`HuntLoop.state`, the Mover), not a fix per caller.
- *The large swing and the Dig as area hits* (`AREA_R` 2 each, from the scenario specials as the other area hitters).
- **Open:** the Dig onto Logan himself can't be stood out of (it comes up on its target): both 48-run deaths had a
  nest crab's 75-84 in their last 15 s. [INFERENCE] A step away at its '*digs*' doesn't help; a potion or the retreat
  timed to its 1.5-2.1 s might.

## The Ossuary in the simulator (user 2026-10-28: "add it to the combat simulator")

Built from the user's three visits that day (docs/SIM.md "The Ossuary", docs/NOTES.md "The Ossuary: three visits by
hand"). Decisions:

- **Level 3 is the hunting level.** Logan spent 1,355 of his 2,649 s inside there; levels 1-2 are the way in, level 4
  (322 s) a side trip. Levels 2 and 4 run with `--area` and the runner flags in the scenario's `runner.note`.
- **A dungeon is a list of boxes** (`dungeon.boxes`, `calibration.boxes`), every scenario cut over. Level 4 lies 700
  tiles from the rest; one box over all four would take in Nusero, Pulma and Covetous and forbid recalls there.
  Rejected: a second scenario for level 4 (one dungeon, one recall rule, one teleporter graph for the reds).
- **The emote-then-hit specials are `burst`s with `delay_s`, `repeat` and `chance`**, not a kind each. Six of the
  nine measured specials are one area or target hit a fixed time after an emote; the hierarch's channel is that hit
  repeated. One new kind, `death_brood`, for the apophite spirit's asps (a death has no cooldown to hang on).
- **Our pets walk through each other and us, in every scenario.** Live they share a tile half the time; here they
  blocked each other, and on level 1's one-wide ramp the walk in deadlocked in 6 of 8 runs. This changes the other
  scenarios' runs a little (no batch rerun of them for it).
- **The kb's missing types by hand** (huntress, fire priest, spirit asp, pocket beetle, plague frog), each with its
  evidence and `missing` fields, as the Mausoleum's Maus 4 entries were. A `kb_scan` rerun drops them; re-add them
  then.
- **Not done**, with the evidence for the next session: the six specials not measured enough, other players and reds
  (no `others`/`reds` blocks), the walk-in wait on the ramp (a runner change, not the simulator's).

## Oxygen: a breath on level 1 or a dolphin on level 3 (user 2026-10-28; built the same day)

User, during the first Tidal Tomb level 2 shift: "The fact the runner doesn't handle oxygen is going to become a
problem. We're fine right now but its going to need to either go back to level 1 for a breath or fight to the dolphins
on level 3 in the future." Today the runner reads no oxygen. Past 732 s under water, Logan and the pets lose hits every
6 s down to 66 % (docs/SIM.md "Oxygen"), and after ~24 min [INFERENCE] down to 33 %. That eats the heal margin of a
long crawl on level 2 or 3. The user then asked for it built ("Agreed. Implement this plan."); decisions below.

What the rule can stand on, all measured (docs/NOTES.md "Oxygen, measured", "The Recovery Dolphin reaches 12 tiles"):

- **The clock:**
  - The server's 'Oxygen' gump 0xEC5F8754 comes every 6 s.
  - Its second bar's art steps down from 28135 every 30.25 s, 24 steps to the deprivation at 732 s.
  - It is back at 28135 on level 1.
  - Or the runner counts its own seconds under water since the last refill: the simulator's `oxygen.underwater` boxes.
- **The refills:**
  - Level 1 (and the outside).
  - A Recovery Dolphin within 12 tiles, with "Your oxygen is refilled." every ~5.25 s. Dolphins are known on level 3
    at (7164, 2210) by the main arrival, (7088, 2185) by the west entrance, and (7321, 2154) by the boss approach.
- **The two options the user named:**
  1. **A breath on level 1:** from level 2, up a pair to level 1, a moment there, then back down. The pair measured
     live is the west strip: up from (6834, 2080) at 11:13:31, down from (6609, 2059) at 11:13:39, the gump full on
     level 1 (docs/NOTES.md "Tidal Tomb"). The arrival pair's way up to (6718, 1984) is the scenario's guess, not seen
     live (docs/SIM.md open item 11).
  2. **The dolphins on level 3:** from level 2, fighting through to a level 3 entrance by the north strip (6884, 1929)
     or the north-west corner (6821, 1910), each with a dolphin near its arrival. On level 3, back to the nearest
     dolphin.
  - **The leg to a refill is fought, not walked like a leave** (user 2026-10-28: "I get the sense the runner will die
    to the gauntlet of mobs on the way that it probably can't just run past. I would prefer it just fight normally to
    the easiest oxygen refill to get to"). The same evidence: the walk-in across level 2 killed all 8 level 3 runs
    before `--fight-via` (2026-10-26), and the time-up walk across it 2 of 9. So it is `walk_in_fought`'s leg with
    the refill as its end: pets on "All Guard Me", `combat_step` each round, the crawl's rejoin, "All Follow Me" only
    at the hop once nothing fights. "Easiest" is the ground to cross (what's alive or respawned on it), not the
    distance. Deprivation is capped (66 % until the major stage, ~24 min [INFERENCE]), so the leg starts early and may
    arrive deprived rather than break off fights.
- **The simulator models both already:** oxygen, the dolphins and level 1's refill (`World._oxygen`,
  `test_tidal_oxygen`, `test_tidal_dolphin`). Whichever option is built is measured there first.

**Built** (docs/HUNT_LOOP.md "Oxygen", `harness/oxygen.py`; docs/SIM.md "Oxygen: the refill legs"):

- **The clock from the server's gump, held by our own count.** The count since the last refill (dry ground, the refill
  line) is clamped to what the 'Oxygen' gump's bars allow, with the bars' timing fitted on the store's 644 gumps
  ((k - 1) x 30.1 + 10 s; replayed, the live deprivation came out at 733 s for 732). Rejected: our count alone (a run
  started under water, the overseer's restart from inside, would read 0) and the bars alone (30 s steps; the count is
  finer between them).
- **The refills are data in the simulator's scenario** (`oxygen.hops`, `oxygen.dolphins`), read by the runner as
  `tamer.py` reads the Mausoleum's spawn spots: one copy of the boxes and the evidence. Rejected: a runner data file
  (two copies of the boxes to keep in step) and runner flags (a forgotten flag live is no rule).
- **Only refills in our own underwater box.** A level 2 hunt never goes down to a level 3 dolphin: the level 3 arrival
  is where the giant astral zoa killed most simulated level 3 runs.
- **A reach cap:** a refill whose leg can't end before the deprivation even from 300 s under is none; the step pace
  (3.0 s) is the fought legs' 75th percentile.
- **The leave rules stay on during a leg** (`oxygen_bail`), unlike the walk in's fought legs: the leg is on the hunting
  level, where a hurt Logan leaves.
- **Heals capped at the deprivation's floor while deprived**: above 66 % the drain takes back what a potion gives.
- **Level 2's west strip is not taken (`skip`).** Measured, 48 runs a side on one tree: its legs walk the crawl from
  the arrival into the strip's arrival, the shooters' ground; runs with a death 12 -> 19 (10 during a leg), the
  deprivation no lower. This goes against the plan's option 1 as written; it keeps the user's point (fight to a refill
  only if getting there doesn't kill us).
- **Level 2's arrival stairs are** (2026-10-29: the user walked them both ways on request, 3 tiles wide; option 1 with
  the stairs the runner already comes down). 48 runs a side: Logan's deprivation damage 5,056 -> 520, runs deprived
  36 -> 4, runs with a death 14 -> 11, none during a leg, legs a median 115 s.
- **Level 3's dolphins are.** Logan's deprivation damage 4,452 -> 2,435, the pets' 57,207 -> 29,657, runs with a death
  28 -> 25, banked an hour +13 %.
- **Open:** the fought walk across level 2 to level 3 (11-20 game minutes in the simulator) runs past 732 s with no
  refill leg; the major stage (~24 min, 33 %) is unmeasured.

## Simulator speed: two caches, no Rust port (user 2026-10-28)

The user asked how much faster the simulator would run ported to Rust. A cProfile of one 30-min Mausoleum run
(docs/SIM.md "Speed") put the simulated world (`World.run_until` and its ticks) at ~15 % of the CPU; the rest is the
Python runner it drives. Decisions:

- *No Rust port of `harness/sim/`*: even a world that took no time at all saves ~15 % (Amdahl). Porting the runner too
  would end the simulator's point (the unmodified `loop_hunt.HuntLoop` under test) unless the live runner moved with it.
- *Fix the two hot spots in Python, exactly*: `speech.get_keywords` caches its answer per text (it scanned all 6,113
  speech.mul entries on every pet command, ~26 % of the CPU); `pathfind.Walk` caches `can_walk` per start tile and
  `Walkers.get` drops only the tiles whose ground items changed (it dropped the whole tile cache on every state read,
  14,121 times a run). Both are deterministic: 6 runs on two scenarios came out identical before and after, 1.61x less
  CPU. Live code shares both (the proxy's z tracking, the Mover's planner), so it gets the same speed-up.
- *Rejected for now*: `pathfind` as a compiled extension (PyO3). After the caches it is the next candidate; profile
  again first.

## Walk rules: a surface at z -128 stands; confirmed moves are checked with what the server sent (2026-10-28)

`test_pathfind`'s "all server-confirmed moves are walkable" failed with 36 of 275,040 (docs/NOTES.md "Mounted movement
and player houses" and "z while walking comes from the map"). Decisions:

- *`pathfind.Walk.new_z` returns None for "no surface", not -128.* Upstream's sbyte sentinel refused a tile standing
  only at -128; Outlands' z is an int, and the client sent and the server confirmed two Tidal Tomb diagonals past
  such a corner (6887,1980). Live and simulated walks may now use the 16,074 non-impassable land cells at -128 (39 of
  them reachable from Tidal Tomb's walked floor). Rejected: a server-only exception for the two moves (the client
  sent them, so the client's rules allow them).
- *A confirmed move the bare map refuses is checked with the ground items and houses of a session that confirmed it.*
  34 of the 36 were on a player house's floor at Anchors Rest; the runners already walk with houses
  (`Walkers.get`). Rejected: a `SERVER_ONLY_MOVES` entry per move (the server's map isn't different, the test's was)
  and every capture's items at once (a house gone later would stand in other sessions' moves).

## A hit centred on its target: keep 4 off the pets a zoa can target (Logan dead on Tidal Tomb level 2, 2026-10-29)

Live 12:36:55 Logan died 3 from his pets, 8 from a combustive zoa that couldn't see him: its scorch lands on the pet it
targets and everyone within 3 (157 of 168 there, 0 of 181 farther, eight captures), and its lightning took the rest
(docs/NOTES.md "Tidal Tomb: level 2 death 2026-10-29 12:36"). Decisions:

- *Each live pet within a target-centred area hitter's reach is an area zone of radius + 1* (`tamer.TARGET_AREA` from
  the scenarios' `center: "target"` bursts with a radius: the combustive and astral zoas, plain and giant). It reuses
  the stand-off, `ClearOf` and the Mover's `avoid` tiles, as the bedrock's cone does; no new mover. The astral zoas come
  with it: the same shape, no rule before. Rejected: a larger caster reach for the zoa (its shot isn't what hit him; 8
  out of its sight was already "out of its shot"), and keeping Logan beyond 12 of an awake zoa (beyond the vet
  supplies' reach of pets fighting it from 10).
- *The cover, the reposition and the bard step pick tiles clear of every area zone*, not only off its centres: in the
  simulator they put Logan back 3 from the pets and the stand-off walked him out again, round and round.
- *A heal potion refused with 500119 is clicked again and never counted as a drink* (the moongate's rule since
  2026-10-19). Rejected for now: a gap before every item use (the vet supplies, the potion and the cure each click on
  their own; one retry covers the refusal at the cost of 0.6 s).
- *A patrol refused again before a step holds 0.4 s*, as every other crawl wait.
- *Not built:* the zoa's lightning (4 volleys seen, up to 3 of us at once, no warning), the scorch's rising
  susceptibility, a leave or retreat rule for "hurt with an awake caster around" (the leave line would not have fired:
  56, then 62 hits before a 63 in one instant; out of the scorch the lightning alone took 52).

## Tidal Tomb level 2 deaths: the shooters' real hits, the lightning without a zone, the monster on us (2026-10-29)

The simulator's level 2 deaths (38 of 144 after 1ffae37) were mostly drowned mariners and soulcatchers. Measured on
the nine Tidal Tomb captures (docs/NOTES.md "Tidal Tomb: the zoas' lightning, the scorch's susceptibility, the
shooters' hits"), both hit their target and everyone within 1 of it, every 4.0 s (half of the mariner's missing) and
5.7 s, 15-39 and 28-37 on Logan, and Logan was their target 3-5 % of the time; the simulator shot one target 2.5 and
2 times as often, 38-48 on Logan, a fifth of the shots at him. Decisions:

- *Model first, then judge the runner.* The zoas' lightning (every zoa type, on everyone within 4 of its target), the
  scorch's susceptibility (+8 % a stack from the combustive zoas, refreshed, gone ~17 s after the last) and the shooters'
  measured hits are in tidal_tomb.json; "before" is HEAD's runner on that model (with HEAD's `TARGET_AREA` pinned: the
  runner reads its zones from the scenarios). On it level 2's runs with a death fell 42 -> 24 of 144 with no runner
  change: the shooters' share was the model's. The deaths left are fins on Logan (11 of 24), zoas (6), bedrocks (3).
- *No zone for the lightning* (`tamer.TARGET_AREA_EVERY_S` 15): keeping 5 off the pets of every awake zoa for a hit
  every 22-87 s cost more than it saved (24 -> 33 runs with a death, pet deaths 223 -> 271).
- *The shooters' target areas and the monster beside us as the target, together* (docs/HUNT_LOOP.md): 432 runs, runs
  with a death 85 with neither, 101 and 93 with one alone, 69 with both; Logan's damage an hour -12 %; pet deaths +3 %,
  banked an hour -3 %. Alone neither helped; kept as a pair, and the evidence is the simulator's [INFERENCE: how often
  a fin picks Logan live under the runner is unmeasured; the captures have 6 numbers on him with a fin beside him].
- *Not built:* a leave or retreat on a predicted next-hit spike. 20 of the 24 deaths went from 50+ hits to 0 within 5 s
  (a fin's swing, spin and bleeds, a zoa's scorch with its lightning): no hits line that leaves in time would let a
  fight go on; the rule would be a different fight, not a later leave.

## A leg that must arrive goes on past a shy monster after 3 back-offs (live loop on Tidal Tomb level 2, 2026-10-29)

Live 18:13:23-18:14:28 the oxygen leg to level 2's stairs walked into an idle soulcatcher's first sight, the back-off
stepped out, the leg walked back in: 18 times in 63 s, 470 -> 560 s under water, until the user took over. Its 'All
Patrol' was refused: a graphic 0 click names the land, here under the stone stairs and out of sight (14 of 16 Tidal
Tomb patrols so; docs/NOTES.md "Tidal Tomb: the oxygen leg's back-off loop"). Decisions:

- *The simulator answers 'All Patrol' as the server does* (the ground point, as the songs'), then judge the runner:
  on it HEAD's level 2 had the loop 1.6x as often and runs with a death 15 -> 21 of 144.
- *In a fought leg, the 4th back-off from one idle shy monster with no kill since the first goes on past it, the pets
  on guard* (`tamer.BACK_OFF_REPEAT` 3, `Team.faced`): the leg says "fought through", and with every patrol refused
  the back-off had nothing to wait for. Level 2, 144 runs: legs ending past 732 s 55 -> 8, runs deprived 87 -> 46,
  runs with a death 21 -> 18, banked an hour +10 %, pet deaths 229 -> 255 (a batch's spread). Level 3's fought walk,
  48 runs: runs with a death 29 -> 29.
- *Not on the hunt level* (the same rule there: runs with a death 21 -> 25, pet deaths 229 -> 279, banked an hour -1 %),
  though the crawl repeats back-offs too (live 18:09:06-43 13 from a drowned mariner, 2026-10-28 22:09-22:12 32 from a
  soulcatcher at the same stairs).
- *Not built:* the patrol's spot by the point the server checks (our eye to the tile's land, as the simulator now
  answers). With the breaker, level 2's 144 runs: patrols out 107 -> 286, refused 1,172 -> 0, runs with a death 18 ->
  17, pet deaths 255 -> 244, but Logan's damage an hour +12 %, his deprivation 21,112 -> 24,283 and banked an hour
  -9 %; level 3 (48) 29 -> 30 runs with a death. It changes every Tidal Tomb patrol, and the simulator only holds it
  to its own rule (the captures: 19 of 20 clicks): its own decision, with more runs.

## Shooters' and casters' first target: measured, the user's guard-and-step-back holds (2026-10-29)

- *Measured, nothing built yet* (docs/NOTES.md "Shooters' and casters' first target: the delay and the nearest
  (2026-10-29)", `harness/sim/wake_scan.py`): a shy monster flips to war 1.6-3.2 s after first sight (floor 1.6 s,
  602 wakes) and takes the nearest of us in its sight then, by a tile or more (a pet 47 of 49), not the first seen;
  so `first_on_us`'s "first sight takes whoever it sees first" is wrong, and a step-back rule may count on 1.5 s.
- *Built (2026-10-29):* the simulator's wake as measured (8cfcb41), then the user's step-in as a runner rule (a698a73,
  `tamer.Team.step_in`/`step_back`/`step_in_watch`): one idle shy monster without a wide area hit, the pets on guard,
  the step into its sight only with the step back planned, its steps after the onset within 1.5 s less 0.75 s. Kept
  where a step-back tile exists; elsewhere the back-off stays (without it level 2's runs with a death 19 -> 31), the
  patrol stays for the area hitters and a pack, `first_on_us` keeps the Chebyshev rule. Level 2 runs with a death 21 ->
  19, pet deaths 326 -> 264; Maus 3 damage on Logan -10 % (docs/SIM.md "The shy monsters' wake, and the step-in").
- *Built (2026-10-29 evening):* the pass in a fought leg (`tamer.Team.pass_by`, `pass_plan`, `PASS`): after 3
  back-offs from one idle shy monster, a walk of up to 12 steps planned with the pets followed so it wakes on a pet
  (walked to just before the onset, the pets let close up, planned again), else on past it as before.
  `test_walk_in_fought` passes (that layout's seeds 1-12: 5 -> 12 of 12). Level 2, three 144-run blocks: the
  mariners' and soulcatchers' wakes on Logan within 2.5 tiles 152 -> 118, Logan's deprivation -10 %, pet deaths 796 ->
  746, banked -3 %, runs with a death 56 -> 71; with no memory carried between runs (`--fresh`) 13 -> 13, the runs it
  changes 3 -> 3 deaths. Level 3 (48 a side, two blocks): runs with a death 30 -> 29 and 36 -> 30, the crossing slower.
- *Decided (user 2026-10-30):* the pass off by default (`tamer.PASS = False`), the step-in kept. The career blocks' rise
  (56 -> 71 runs with a death, every block) outweighs the fewer wakes on Logan until its cause is traced; a fought leg
  goes on past after 3 back-offs as before. `test_walk_in_fought` turns the pass on to keep the mechanism checked.
- *Open:* two idle shy monsters together (not built: each wake takes the nearest, but the plan has to hold for both and
  two shooters land on the pets at once; the pull budget and the patrol keep a pack); the career blocks' rise in runs
  with a death with the pass (not traced: it came in runs with no pass of their own, through the store a career
  keeps); the margin against live (0.35 s gave fewer deaths, 16, but more oxygen damage and 8 % less banked: watch
  live step-ins first).

## The giant pincer crab's hold modelled; the pets first on the walk out (Logan dead on Tidal Tomb level 2, 2026-10-30 18:29)

Live 18:29 the planned walk out across level 2 passed 2 tiles from an idle giant pincer crab ahead of the pets; its
pinch (Entangle, 6 s) held Logan while a drowned mariner and a voidfin woke on him (docs/NOTES.md "Tidal Tomb: level 2
death 2026-10-30 18:29"). Decisions:

- *Model first* (built): the crab's swing from 2 tiles, its measured numbers and 3.36 s period, the pinch's number and
  hold, the hold readable by the runner as live (buff 20 with its end). On it the live layout reproduces the cause.
- *Two wider rules first, not kept.* Both stop the layout's cause and lost level 2's 144-run batch: the leave from under
  water as a fought leg (runs with a death 21 -> 48, planned-leave deaths 1 -> 31, oxygen on Logan +57 %), and the
  walk out 3 tiles wide of every idle monster in view (21 -> 33, 1 -> 8, banked -19 %) (docs/SIM.md "Tidal Tomb: the
  giant pincer crab's pinch"). On HEAD's runner the batch has 1 planned-leave death in 144 runs: the simulator has
  almost nothing for a walk-out rule to win and much to lose.
- *Built (user-approved "narrow rule", 2026-10-30 evening):* the pets first by an idle melee monster on the planned
  walk to the leave gate (`HuntLoop.idle_ahead`, `lead_pets`: stop when the next 6 route tiles come within its reach
  plus 1 with Logan in front and its wake under way, the step-in's step back so a pet is the nearest, a hold up to 6 s
  for its wake). The layout: the crab on a pet 24 of 24, no pinch on Logan (7 before). Level 2's runs with a death
  51 -> 60 of 288, within the two blocks' spread (docs/SIM.md "Tidal Tomb: the pets first by an idle melee monster").
  Scoped to the walk out: on the walk in too (`LEAD_IN`) 95 % of the leads were there, which has no stand for what
  wakes; same batch deaths (50 of 288), but test_ossuary_hunt diverged and timed out on its leave.
- *Decided (user 2026-10-30):* `loop_hunt.LEAD` stays on, though every level 1 and 2 batch leaned the wrong way (level 2
  51 -> 60, its planned-leave deaths 4 -> 11 with 6 of the 8 extra in block 2 without a stop; level 1 1 -> 3 of 96;
  Maus 3 1 -> 0). Live level 2 runs, the next ones planned there, are its test: watch the "for the pets to lead" lines
  and whether the crab wakes on a pet; turn it off if a lead goes wrong or level 2 stays off for long.
- *Open:* the crab with the pets outside its range at the stop (no step-back tile: today's walk); whether a melee
  monster live takes the nearest at its wake as the simulator has it (the crab struck from 2 with its war flag after
  the hit: a later, nearer wake would let the 6 s hold run out "still idle" and the walk go on past it); the stand for a
  shy monster ending with it alive (18:29:25.9, "All Follow Me" pulled the pets off a biolumen shellpod at 42 %); a
  stand-off tried while held (`walk_in_fight` waits in `Mover.wait_held`).

## No route in a red flight: out of their sight meanwhile (Logan dead on Maus 3, 2026-10-30 23:43)

Live a red's Wall of Stone sealed the east wing's only corridor west; the flight found no route and stood 4.7 s
"looking again (n of 6)" beside three casters (docs/NOTES.md "Walled in the east wing").

- *Cause:* a hard block (the wall, a field to the Mover), not the soft zones: those are route costs and never seal a
  route. No route existed; relaxing zones would have found none.
- *Built:* `flee_out` takes cover meanwhile (`cover_tile`: from ranged reds the tile fewest of them see, then the fewest
  steps; from melee ones the farthest) and waits out a field that cuts the way without counting it (`field_cut`); the
  leave's start plans through fields when walled already. The user's dead-end rule is unchanged. `sim/fight2343.py`:
  the stall 26.9 s in 11 of 24 runs -> 1.2 s in 1; deaths 21 (+3 left alive by the abort) -> 24 of 24.
- *Open:* the rope aimed past a wall that rose between its plan and its target (the replay's "rope to (5252, 653) …
  no jump", 21 of 24 runs before and after); fighting back (the pets on a red) when sealed in with them.

## Marinerbane's room in the simulator (user 2026-10-31: "flesh out the simulator for TT with anything new")

The user went deeper into Tidal Tomb level 3 with his guild on 2026-10-30. That was Marinerbane's room, its mini boss
killed three times (docs/NOTES.md "Tidal Tomb: Marinerbane's room"; docs/SIM.md "Tidal Tomb: Marinerbane's room").

- *Built (simulator):*
  - The room, behind level 3's west wall: the teleporters both ways, the dolphin, under water, in the dungeon box.
  - Marinerbane as a spawn that comes back 300 s after its death.
  - Its moves on one timer (`rotation`): waves of deep dwellers that die with it, the glowing barrels' bursts, and the
    cannon crew, feelers and lash as emotes only (nothing of them seen on us).
  - '*Increases in Size*' (+40,000 hits a damaging player), and three new spawns on level 3's west wall.
- *The teleporters into `teleporters.json`* (the room's, level 3's west entrance, the boss approach): every Mover now
  routes around them. Before, a walk across level 3's west wall or north end could have stepped into a boss room.
- *Not calibrated from this capture:* calibrate.py would count the guild's bonded pets as ours and sum one shot's
  numbers on three of us into one swing. Its facts came from a scratch pass, each number with its note.
- *Not changed:* the runner, the level 3 command, and Marinerbane's name ("a marinerbane" in the wiki kb; live
  "Marinerbane"). The runner never goes there: the room is a teleporter away, and no command uses it.
- *Open:* the cannon crew's target and damage, the feelers and the lash. Marinerbane's own swing. The Undermind's
  boss room. The stale `tidal_tomb_observed.json` (generated before 20261027_220057 was complete).

## Maus 3 2026-10-31: dead at 11:57, near-deaths at 11:23 and 11:49 (docs/NOTES.md "Maus 3 near-deaths")

- *Causes:* 11:57 a hidden vampiric consort beside its known spawn spot (114 of 135 on Logan) after the approach of a
  forgotten soul walked him 3 ahead of the pets; 11:49 two hidden shade wolves and the soul, the retreat 10 off the pets;
  11:23 a corpsebride's wail twice from 4 tiles, a countess's Flamestrike from 10, bleed ticks. In all three the leave
  fired only below `HEAL_IN_PLACE_FLOOR` 0.15 (vet supplies in the pack are always a heal coming), read in 0.4 s.
- *Decided:* the backspawn spots seeded on arrival (`Team.seed_near`; dead since the setup moved home, 2026-10-21); the
  retreat by the pets (`retreat_tile`'s second key); the wail's far ring (`AREA_FAR`). Each by its replay
  (sim/fight1031.py) and a test (test_sim `test_backspawn_on_arrival`, `test_retreat_by_the_pets`,
  `test_wail_far_reach`, failing before).
- *Rejected:* the forgotten soul as shy (sim/soul_scan.py: it walks in like the countess, a pet its first target 149 of
  179); a higher leave floor (the leaves walked toward the danger; the heals saved him).
- *Open (then):* the approach past a stealther's spawn spot (decided below); a stealther seen hiding and an 0xAA naming
  an unseen serial as warnings; the simulator's idle stealthers waiting hidden (the consort's respawn done below); the
  soul's multi-target bolt and spell mix; pet deaths on the live team 5 -> 11 in the batch with the three fixes (12
  without the far ring: from the backspawn seeding or the retreat; most with Logan 7-9 off the pet, out of the vet's
  reach).

## The closed area round a vampiric consort's spawn spot (user 2026-10-31: "Fix the approach")

- *Decided:* never unguarded near a live consort spawn spot (the user: "I'm not going to stand unguarded near where the
  vampiric consort spawns"): a tile within `aggro.STEALTH_CLOSED_R` 5 of one, with no pet within 1 or nearer the spot,
  is closed; the approach stops short (`Team.closed_step`), the tile pickers cost it 100 (`View.stand_score`), the loot
  waits, the patrol skips its waypoints and routes round it (`Crawl.closed_wp`, `Team.around_stealth`); a consort death
  seen within 12 opens it for 180 s (`Team.spot_live`). 5 from sim/stealth_scan.py (bites from hiding by a spot: the
  victim 1-4 off). The consort only, by danger (the user: the shade wolf "isn't a huge deal"); the wolf and the
  nightstalker keep the soft backspawn score. Simulator: the consort's bleed and her lurk after a respawn.
- *Evidence:* fight1031 1157 bitten 19 -> 0 of 24; batches within the noise (docs/SIM.md "The consort's closed area").
  At 11:57 she was a fresh respawn 2 off her known spot, not pulled off it; so the rule would have held Logan at 4.
- *Not decided:* the shade wolf's spots out of the soft score (`aggro.BACKSPAWN_OFF`): no difference in the batch;
  the user's call. *Open:* her usual place (the east corridor near (5260, 628), 55 of 58 sightings) is a spot only once
  seen this run or killed there; walks to a guarded tile inside the area can cross unguarded ones; `STEALTH_RESPAWN_MIN_S`
  180 s is half of the one respawn seen [INFERENCE]; the 0xAA warning.

## The easiest of several leave gates (user 2026-10-31)

User: "One thing I noticed is that the runner leaves by the yellow gate near the entrance, but if its time to leave it
could leave by the easiest gate to get to."

- *Decided:* `--leave-gate` repeats (one given: as before, no choice, no extra plan). The leave picks from where Logan
  stands (`HuntLoop.pick_gate`): a red's flight first a gate whose way passes none of them, then one not only through
  a spell field, then the least route steps plus the walk's danger (the Mover's soft zones as the planner weighs them;
  20 steps an awake monster, 10 an idle one, whose aggro range and sight take in the way). Chosen again when the walk
  fails or a new hostile player comes onto its way; the red's dead end only when every gate's way passes them; a menu
  without `--gate-dest` closed and the next one taken. Gates must share one level and `--gate-dest` (the crawl refuses
  mixed floors; `--leave-via` reaches them all a level up). Tidal Tomb level 1's command gives its three golden gates.
- *Ordering:* a planned leave picks before its LEAVING line (the user's example names the gate there); a hurt leave or
  a red's picks after its first acts (cursor, disengage): three routes cost ~0.2 s.
- *Evidence:* the simulator, 288 runs a side (docs/SIM.md "Tidal Tomb: level 1's three golden gates"): leave walk
  median 139.5 -> 80 steps, banked an hour 111,229 -> 116,711, runs with a death 2 -> 2.
- *Rejected:* the nearest by Chebyshev distance (level 1's walls make the route the measure); a fixed preference
  order (where the crawl ends changes which gate is nearest: the arrival's gate was the easiest 1 time in 509).
- *Open:* 0x4003D7B7's menu never opened live [INFERENCE: Totem on it]; the first live run's "the moongate at …: no
  'Totem' on its menu" line would show otherwise. GATE_IDLE_COST and GATE_AWAKE_COST are not tuned (the batch's
  danger was alike on every gate's way in the traced leaves).

## Risks

- **Protocol drift**: Outlands patches frequently (client is days old at research time). Parser must be tolerant of unknown packets (log-and-forward) with a packet-ID registry that's easy to update.
- **Custom login/auth**: the OutlandsID HTTPS handshake is not yet mapped at byte level; proxy only needs the post-auth game connection, but settings/login responses may bind the session to the original destination — validate in Phase 1.
- **Behavioral statistics**: even perfect protocol emulation can be detected by play patterns; pacing, session length, and task selection are the mitigation (§8 rules).
- **Staff attention on Test Shard**: keep activity non-disruptive to avoid drawing staff notice (detection risk).

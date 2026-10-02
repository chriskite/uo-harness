# Handoff — uo-harness (start here)

**What this is:** an AI agent harness that plays UO Outlands via a localhost proxy
(WinDivert NAT → Python proxy → game server). Character TestWorth.
Repo: `chriskite/uo-harness`. Read `AGENTS.md` (standing rules: record-and-commit
knowledge, never touch the client process/install dir, no in-game harness
mentions), then this file, then the docs below as needed.

## State (2026-09-29)

**Works, proven live:** interception, cipher, injected actions (speech, dclick,
gumps, spells, item queries), agent walking under fix A (~1 step per 5 s).

**2026-09-29, major correction: the S2C decode was wrong from day one.** The server→client
stream is XORed with prelude byte 11 before Huffman (client: `ProcessRecv @
0x140145600`). The prelude is 13 bytes, not 19. Old decodes produced garbage that docs called a
"custom dialect". Corrected decoder: `harness/uo/s2c.py`; 53 277/53 277 packets frame
exactly across all captures. The content is standard UO. See docs/CIPHER.md §4. The world
model + replay were migrated to it (all 18 captures: 0 length mismatches, 0 parse failures;
9 parser layouts fixed; docs/WORLDMODEL.md, PROTOCOL.md corrected; WORLDSTATE.md superseded).

**Movement (real S2C, docs/MOVEMENT.md "▶ Current model"):**
- Walk = `02 <dir|run> <seq> <key u32be>`. Token = the latest `BF 0001` seed
  (login seeds 5,6,7,8; resync reply seed 1). Server confirms walks with `22 seq 01`.
  Rejections are silent and don't reset the server's seq. Resyncs < ~5 s apart are ignored.
- Client lockout: foreign confirms → client bad step → frozen walker.
- **Fix B implemented (`harness/proxy.py` MoveAuthority), offline-proven
  (`test_movement.py`):** hide agent confirms from the client, map client confirms
  back to client seqs, ladder follows seeds (rewinds on silent rejection), pacing
  0.2/0.4 s. **Re-anchor is client-only (user decision 2026-09-29):** ~0.5 s after
  walking stops, the proxy hands the client a fabricated S2C `0x21` DenyWalk at the
  tracked server-true position. **The proxy sends nothing to the server on its own.**
  Known limit: z = last server-reported z.

## Next steps (in order)

1. ~~Live-verify fix B~~ **VALIDATED (session_20260929_161433):** CLI and client
   walking both fully work; 0 resyncs, 0 proxy-originated server packets. Blocked
   moves get a real server `0x21` deny (handled).
2. ~~Closed-loop bank run~~ **DONE LIVE (session_20260929_163420), Phase 3 complete.**
   `harness/errand_bank.py --start 1963,2597`: start → banker (label heard) → 13 steps →
   "bank" → bank box opened → 13 steps back to exactly the start; 26 s, 0 blocked, 0 resyncs,
   0 proxy-originated server packets. Offline: `test_errand.py`.
3. **Next: Phase 4 (agent runtime)**, an LLM planner over the state port + action/skill library,
   with safety rails (docs/PLAN.md). First workload: the lumberjack → bank loop
   (docs/LUMBER_LOOP.md; until 2026-10-01 it stored in an inn rental room):
   - M0 done: the demo `20260929_204225` → `harness/data/loops/lumber.json`, pinned by
     `harness/test_loop_demo.py`.
   - Runner `harness/loop_lumber.py` built and offline-proven (`test_loop_lumber.py`).
     **Since 2026-10-01 (user decision, LUMBER_LOOP.md §12.5) each trip ends at the banker:
     say `bank`, drop the boards into the bank box.** It runs on Shelter with a fresh Young
     character and is offline-proven only; the live runs below used the room version.
  - **Live proof ✅:** attempt 2c (22:27), 1 trip, 46 boards. **Run 3 (22:47): 3 trips, 50
    boards in 8.4 min, live captcha detection works** (LUMBER_LOOP.md §13). Next: optimization
     (§6) from the memory store's episodes and yields (baseline ≈355 boards/active hour).
   - The visualizer shows what the agent is trying to do ("Heading to tree at …", "Chopping …",
     "Going to the bank: …") in the **Agent** panel and as a map reticle (VISUALIZER.md §2.3). The live
     proxy must be restarted once to carry intents; the runners work either way.
   - **Next phase planned (2026-09-29 night): [`docs/ROADMAP.md`](docs/ROADMAP.md).** Built:
     - the overseer bus + `harness/ctl.py` (docs/OVERSEER.md)
     - the viz Overseer tab and Jobs page
     - `threats.py`, `ledger.py` and the Tracking parsers
     - loop guards: captcha, threat, theft and death junctures

     The ROADMAP ends with the user's open questions and the demo captures needed.
4. Optional: map-based z for re-anchors (stairs/slopes).
5. ~~Visualizer~~ **Phase A BUILT (docs/VISUALIZER.md §9)**: read-only `viz_server.py`
   (live on the state port, or an exact offline replay of a capture) plus a React + TSX frontend
   built with Bun. The live proxy needs a restart to serve proxy events, traffic, labels and
   diagnostics (it was started before these landed; a restart disconnects the client).

## Operate

- Proxy + divert NAT in one go (skips whatever is already up; one UAC prompt):
  `powershell -ExecutionPolicy Bypass -File start_proxy_nat.ps1 [-RestartProxy] [-RestartNat]`
- Proxy (needed, restart if down):
  `python harness/proxy.py --nat-lookup-port 25943 --upstream-bind 0.0.0.0 --upstream-bind-port 25940 --logdir logs --memory-db harness/data/harness.db`
  (`--nat-lookup-port`: dial whichever server IP the client chose, as reported by divert_nat; docs/INTERCEPTION.md)
  (`--memory-db` = the durable harness memory, docs/MEMORY.md; gitignored runtime data)
  (Python is at `C:\Users\chris\AppData\Local\Programs\Python\Python313\python.exe`)
- Divert NAT (needed, elevated): `powershell -Verb RunAs restart_divert.ps1`
- Launch game (elevated): `powershell -Verb RunAs launch_game.ps1`
- Watch packets: `python harness/tail_log.py`
- Live state (movement truth + world model + events): JSON lines on 127.0.0.1:25942,
  request `{"op":"state","since":N}` (proxy `--state-port`).
- Drive walks: `python harness/walk_cli.py` (arrows=walk, space=run/walk, q=quit)
- Agent gate (pause / kill / forced breaks / 8 h daily budget; state in `logs/agent_budget.json`):
  `python harness/agent_gate.py [status|pause|resume|kill|rearm]`. `rearm` (clears a kill) is
  CLI-only; the visualizer offers pause/resume/kill.
- Bank errand: `python harness/errand_bank.py [--start x,y]`.
- Harness memory: `python harness/memory.py stats`; backfill captures the store hasn't seen with
  `python harness/memory.py ingest`; 2D plan on stored walk evidence:
  `python harness/nav.py plan x1,y1 x2,y2 [--facet F]`.
- Lumber loop: `python harness/loop_lumber.py --trips 1`. On a captcha it pauses and beeps
  for you to solve it in the client (captcha mode `human`, the default); the viz header's
  `captcha [human|auto]` toggle switches to auto-solve from the gump layout. Harvest outcomes
  and episodes go to the memory store.
- Speech triage for harvest jobs (optional; without it speech holds just carry no Laya verdict):
  `python harness/triage.py serve` in its own terminal (laya-serve on 127.0.0.1:25970, CPU,
  listening ~10 s after start, ~2 GB RAM). Stop it with Ctrl+C: killing only the Python wrapper
  leaves `laya-serve.exe` listening. Check a line by hand: `python harness/triage.py judge "u there?"`.
  Setup and numbers: docs/NOTES.md "Laya speech triage".
- Demonstration timeline (offline, read-only): `python harness/loop_mine.py timeline <TAG>
  [--labels]`. Replays `logs/session_<TAG>.*` and prints actions, gumps, menus, cliloc
  messages rendered from Cliloc.enu, and container amount changes.
- Visualizer: build once `cd viz && bun install && bun run build`; then
  `python harness/viz_server.py --live` (or `--replay <TAG> [--rate 8]`) → http://127.0.0.1:8080/.
  Terrain underlay comes from the install dir's `facet00.mul` (read-only; `--no-facet` to skip).
  The viz only observes, except for the agent-gate buttons, the captcha mode toggle and chat.
- Tests: `python test_proxy.py`, `python test_movement.py`, `python test_errand.py`,
  `python harness/test_world.py`, `python harness/test_world_replay.py`,
  `python harness/test_actions.py`, `python harness/test_nav.py`, `python harness/test_viz.py`,
  `python harness/test_agent_gate.py`, `python harness/test_loop_demo.py`, `python harness/test_facet.py`,
  `python test_loop_lumber.py` (~1.5 min), `python harness/test_mover.py`, `python harness/test_triage.py`,
  `cd viz && bun test && bun run typecheck` (all use private control/state ports; safe while
  the live proxy runs).
- Push works via SSH alias `github.com-uoharness` (deploy key `~/.ssh/uo_harness_deploy`).

## Doc map

`docs/INTERCEPTION.md` (NAT chain) · `docs/CIPHER.md` (crypto) · `docs/MOVEMENT.md`
(movement protocol + current state) · `docs/PROTOCOL.md` (packets) ·
`docs/WORLDMODEL.md` (state layouts) · `docs/PLAN.md` (phases) ·
`docs/LUMBER_LOOP.md` (proposed first agent loop) ·
`docs/NOTES.md` (ops gotchas) · `ANTICHEAT.md` (detection surfaces)

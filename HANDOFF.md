# Handoff — uo-harness (start here)

**What this is:** an AI agent harness that plays UO Outlands via a localhost proxy
(WinDivert NAT → Python proxy → game server). Target: Test Shard only, character
TestWorth. Repo: `chriskite/uo-harness`. Read `AGENTS.md` (standing rules, incl.
record-and-commit knowledge, Test-Shard-only, no production, no in-game harness
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
   with safety rails (docs/PLAN.md). First workload: the lumberjack → inn-room loop
   (docs/LUMBER_LOOP.md):
   - M0 done: the demo `20260929_204225` → `harness/data/loops/lumber.json`, pinned by
     `harness/test_loop_demo.py`.
   - Runner `harness/loop_lumber.py` built and offline-proven (`test_loop_lumber.py`).
   - **Next: the live proof, 1 trip with no deeds, while the user is at the client for the
     captcha** (LUMBER_LOOP.md §13).
4. Optional: map-based z for re-anchors (stairs/slopes).
5. ~~Visualizer~~ **Phase A BUILT (docs/VISUALIZER.md §9)**: read-only `viz_server.py`
   (live on the state port, or an exact offline replay of a capture) plus a React + TSX frontend
   built with Bun. The live proxy needs a restart to serve proxy events, traffic, labels and
   diagnostics (it was started before these landed; a restart disconnects the client).

## Operate

- Proxy (needed, restart if down):
  `python harness/proxy.py --upstream-host 74.91.115.123 --upstream-bind 0.0.0.0 --upstream-bind-port 25940 --logdir logs`
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
- Bank errand: `python harness/errand_bank.py [--start x,y]`; walk memory refresh:
  `python harness/nav.py build`.
- Lumber loop (the user must be at the client to solve captchas):
  `python harness/loop_lumber.py --trips 1`. It beeps on a captcha and waits for the human's
  answer. Episodes are written to `harness/data/episodes/lumber.jsonl`.
- Demonstration timeline (offline, read-only): `python harness/loop_mine.py timeline <TAG>
  [--labels]`. Replays `logs/session_<TAG>.*` and prints actions, gumps, menus, cliloc
  messages rendered from Cliloc.enu, and container amount changes.
- Visualizer: build once `cd viz && bun install && bun run build`; then
  `python harness/viz_server.py --live` (or `--replay <TAG> [--rate 8]`) → http://127.0.0.1:8080/.
  Terrain underlay comes from the install dir's `facet00.mul` (read-only; `--no-facet` to skip).
  The viz only observes, except for the agent-gate buttons.
- Tests: `python test_proxy.py`, `python test_movement.py`, `python test_errand.py`,
  `python harness/test_world.py`, `python harness/test_world_replay.py`,
  `python harness/test_actions.py`, `python harness/test_nav.py`, `python harness/test_viz.py`,
  `python harness/test_agent_gate.py`, `python harness/test_loop_demo.py`, `python harness/test_facet.py`,
  `python test_loop_lumber.py` (~1.5 min),
  `cd viz && bun test && bun run typecheck` (all use private control/state ports; safe while
  the live proxy runs).
- Push works via SSH alias `github.com-uoharness` (deploy key `~/.ssh/uo_harness_deploy`).

## Doc map

`docs/INTERCEPTION.md` (NAT chain) · `docs/CIPHER.md` (crypto) · `docs/MOVEMENT.md`
(movement protocol + current state) · `docs/PROTOCOL.md` (packets) ·
`docs/WORLDMODEL.md` (state layouts) · `docs/PLAN.md` (phases) ·
`docs/LUMBER_LOOP.md` (proposed first agent loop) ·
`docs/NOTES.md` (ops gotchas) · `ANTICHEAT.md` (detection surfaces)

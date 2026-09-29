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
model + replay + WORLDSTATE/PROTOCOL/WORLDMODEL docs were built on garbage and are
being migrated (see git log / docs for status).

**Movement (real S2C, docs/MOVEMENT.md "▶ Current model"):**
- Walk = `02 <dir|run> <seq> <key u32be>`. Token = the latest `BF 0001` seed
  (login seeds 5,6,7,8; resync reply seed 1). Server confirms walks with `22 seq 01`.
  Rejections are silent and don't reset the server's seq. Resyncs < ~5 s apart are ignored.
- Client lockout: foreign confirms → client bad step → frozen walker.
- **Fix B implemented (`harness/proxy.py` MoveAuthority), offline-proven
  (`test_movement.py`):** hide agent confirms from the client, map client confirms
  back to client seqs, ladder follows seeds, pacing 0.2/0.4 s, one re-anchor resync
  ~0.5 s after an agent burst (≥ 5.6 s spacing).

## Next steps (in order)

1. Live-verify fix B (attended): walk_cli bursts; no client freeze; character
   snaps to its true position after each burst; arrow keys keep working.
2. Closed-loop bank run, then Phase 4.

## Operate

- Proxy (needed, restart if down):
  `python harness/proxy.py --upstream-host 74.91.115.123 --upstream-bind 0.0.0.0 --upstream-bind-port 25940 --logdir logs`
  (Python is at `C:\Users\chris\AppData\Local\Programs\Python\Python313\python.exe`)
- Divert NAT (needed, elevated): `powershell -Verb RunAs restart_divert.ps1`
- Launch game (elevated): `powershell -Verb RunAs launch_game.ps1`
- Watch packets: `python harness/tail_log.py`
- Drive walks: `python harness/walk_cli.py` (arrows=walk, space=run/walk, q=quit)
- Tests: `python test_proxy.py`, `python test_movement.py`, `python harness/test_world.py`,
  `python harness/test_world_replay.py`, `python harness/test_actions.py` (all use their own
  control ports; safe while the live proxy runs).
- Push works via SSH alias `github.com-uoharness` (deploy key `~/.ssh/uo_harness_deploy`).

## Doc map

`docs/INTERCEPTION.md` (NAT chain) · `docs/CIPHER.md` (crypto) · `docs/MOVEMENT.md`
(movement protocol + current state) · `docs/PROTOCOL.md` (packets) ·
`docs/WORLDMODEL.md` (state layouts) · `docs/PLAN.md` (phases) ·
`docs/NOTES.md` (ops gotchas) · `ANTICHEAT.md` (detection surfaces)

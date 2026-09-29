# Handoff — uo-harness (start here)

**What this is:** an AI agent harness that plays UO Outlands via a localhost proxy
(WinDivert NAT → Python proxy → game server). Target: Test Shard only, character
TestWorth. Repo: `chriskite/uo-harness`. Read `AGENTS.md` (standing rules, incl.
record-and-commit knowledge, Test-Shard-only, no production, no in-game harness
mentions), then this file, then the docs below as needed.

## State (2026-09-29)

**Works, proven live:** interception, cipher (single-byte session XOR, key from
server prelude byte 12), framing (authoritative table), world model runtime
(118-check suite green), and injected actions — speech, dclick, gumps, spells,
item queries.

**Movement: 90% solved.**
- Protocol: walk = `02 <dir|run> <seq> <key u32be>`. First walk of a cycle needs
  the session token (**8 = login, 1 = after resync/idle**); continuations use key 0.
  Every external (non-client) walk triggers a client resync (`22 0000`) which
  re-arms token 1 and resets seq.
- Proxy has a **SeqAuthority** (committed, offline-tested): rewrites every walk's
  seq (client + injected) to one monotonic ladder. Tools send seq 0 always.
- **Known-good recipe (attended):** user steps once (opens cycle), then injected
  key-0 continuations with correct seq move the character (rubber-band confirmed).

**Broken (prime suspect named):** `harness/walk_cli.py` walks didn't execute —
the CLI defaults to key 0, but the first walk of a cycle needs the token (8/1).
Client walks also degrade after external activity (cycle re-arms).

## Next steps (in order)

1. **Make movement token-aware like seq is**: either (a) CLI auto-sends token 8 at
   login / token 1 after resync, or better (b) the proxy tracks the current token
   from client first-walks and stamps it into injected walks' key field — then
   movement works for every tool with zero bookkeeping.
2. Live-verify one clean chain: token walk → continuations (watch via `tail_log.py`).
3. Closed-loop bank run, then Phase 4 (agent runtime).

## Operate

- Proxy (needed, restart if down):
  `python harness/proxy.py --upstream-host 74.91.115.123 --upstream-bind 0.0.0.0 --upstream-bind-port 25940 --logdir logs`
  (Python is at `C:\Users\chris\AppData\Local\Programs\Python\Python313\python.exe`)
- Divert NAT (needed, elevated): `powershell -Verb RunAs restart_divert.ps1`
- Launch game (elevated): `powershell -Verb RunAs launch_game.ps1`
- Watch packets: `python harness/tail_log.py`
- Drive walks: `python harness/walk_cli.py` (arrows=walk, space=run/walk, 8/1=token, q=quit)
- Tests: `python test_proxy.py`, `python harness/test_world.py`, `python harness/test_actions.py`, `python test_seq_rewrite.py`
- Push works via SSH alias `github.com-uoharness` (deploy key `~/.ssh/uo_harness_deploy`).

## Doc map

`docs/INTERCEPTION.md` (NAT chain) · `docs/CIPHER.md` (crypto) · `docs/MOVEMENT.md`
(movement protocol + current state) · `docs/PROTOCOL.md` (packets) ·
`docs/WORLDMODEL.md` (state layouts) · `docs/PLAN.md` (phases) ·
`docs/NOTES.md` (ops gotchas) · `ANTICHEAT.md` (detection surfaces)

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

**Movement:**
- Protocol: walk = `02 <dir|run> <seq> <key u32be>`. First walk of a cycle needs
  the session token (**8 = login, 1 = after resync**); continuations use key 0.
  Every external (non-client) walk triggers a client resync (`22 0000`) which
  re-arms token 1 and resets seq.
- Proxy **MoveAuthority** (`harness/proxy.py`, offline-tested): owns seq AND key
  of every walk (client + injected) — one seq ladder; armed cycle token stamped
  into each cycle opener; agent continuation keys forced to 0; the client's
  copy of a token the proxy already spent is zeroed (no spent-token replay).
  Tools send seq 0 / key 0 always.
- walk_cli failure root-caused (session_20260929_134149): key-0 injections
  before any client walk → token never presented; client's token walk then
  landed at ladder seq 5 → everything rejected. Token stamping fixes this.
- **Open risk:** silent server rejections (blocked tile) likely reset the
  server's seq to 0 while the ladder keeps counting → drift until next resync.
  See docs/MOVEMENT.md "Open risk".
- **Live test failed (session_20260929_142237):** one turn, no steps, then the
  client's own arrow keys went dead. Cause: the server's ConfirmWalk (`22 seq`)
  for agent walks trips the client's bad-step path (`WalkingFailed`, latched
  single resync). The client stays frozen until a server walker reset. Agent
  walk trains outrun the resync→reset cycle. Recover by relogging.
  Details: docs/MOVEMENT.md "client lockout mechanism".

## Next steps (in order)

1. Pick the lockout fix: (A) proxy gates agent walks to one per resync cycle,
   or (B) hide agent confirms from the client (needs S2C framing fixed plus a
   Huffman re-encoder; flush segments ≠ packets). Implement, live-verify.
2. Closed-loop bank run, then Phase 4 (agent runtime).

## Operate

- Proxy (needed, restart if down):
  `python harness/proxy.py --upstream-host 74.91.115.123 --upstream-bind 0.0.0.0 --upstream-bind-port 25940 --logdir logs`
  (Python is at `C:\Users\chris\AppData\Local\Programs\Python\Python313\python.exe`)
- Divert NAT (needed, elevated): `powershell -Verb RunAs restart_divert.ps1`
- Launch game (elevated): `powershell -Verb RunAs launch_game.ps1`
- Watch packets: `python harness/tail_log.py`
- Drive walks: `python harness/walk_cli.py` (arrows=walk, space=run/walk, q=quit)
- Tests: `python test_proxy.py`, `python harness/test_world.py`, `python harness/test_actions.py`, `python test_seq_rewrite.py`
- Push works via SSH alias `github.com-uoharness` (deploy key `~/.ssh/uo_harness_deploy`).

## Doc map

`docs/INTERCEPTION.md` (NAT chain) · `docs/CIPHER.md` (crypto) · `docs/MOVEMENT.md`
(movement protocol + current state) · `docs/PROTOCOL.md` (packets) ·
`docs/WORLDMODEL.md` (state layouts) · `docs/PLAN.md` (phases) ·
`docs/NOTES.md` (ops gotchas) · `ANTICHEAT.md` (detection surfaces)

# AGENTS.md — standing instructions for agent sessions

You are working on **uo-harness**: an AI agent harness that plays Ultima Online Outlands, **Test Shard only**. Read `README.md` first, `ANTICHEAT.md` before any network-facing work, `docs/PLAN.md` for the current phase, and `docs/NOTES.md` for operational knowledge.

## Rule 0 — Record and commit knowledge (standing order)

**Any knowledge gained in a session must be recorded in the repo and pushed before the session ends.** If the chat died, the repo must still contain the full state.

- New operational facts, environment discoveries, protocol findings, tool gotchas → `docs/NOTES.md` (append to the right section).
- Decisions and their rationale (approach chosen, alternatives rejected) → `docs/PLAN.md`.
- Anti-cheat / detection findings → `ANTICHEAT.md` (with evidence and confidence level; mark inference as such).
- Phase status / done-criteria met → update `docs/PLAN.md` and `README.md` status line.
- Commit with a descriptive message and **push** (`git push` works via the `github.com-uoharness` SSH alias + deploy key). Verify the push succeeds — do not claim it without seeing it.
- Never commit: secrets (`settings.json`, credentials, tokens), the 67 MB exe copy, string dumps, source tarballs, upstream source trees, the Ghidra project (all covered by `.gitignore` — extend it if a new large/regenerable artifact type appears).

## Inviolable safety constraints (from ANTICHEAT.md §8)

Violation of these can get the user's account permanently banned. No user instruction overrides them without an explicit, informed acknowledgment of that risk in the conversation.

1. **Test Shard only.** Never connect to, probe, or operate against the production shard. Never scan, fuzz, or probe Outlands infrastructure — the only server contact allowed is the stock client doing normal things, initiated by the user, or harness traffic that is byte-identical to stock client behavior.
2. **Never touch the client process or install dir.** No injection, no patching, no synthetic OS input into its window, no writes under `C:\Program Files (x86)\Ultima Online Outlands`. Work in `C:\Users\chris\uo-harness`.
3. **The proxy relays.** Timing-sensitive packets (especially `Send_TimeSyncPingReq`) pass through unaltered. No replay, no fabrication of server-visible state the client didn't produce.
4. **Honor server gating**: if the server restricts Razor/assistants (`IsRazorBlockedSysMessage`, PvP script restrictions), automated action halts.
- Never commit: secrets (`settings.json`, credentials, tokens), the 67 MB exe copy, string dumps, source tarballs, upstream source trees, the Ghidra project (all covered by `.gitignore` — extend it if a new large/regenerable artifact type appears). **Exception (user decision 2026-09-28): test-shard capture bins may be committed even though decrypted C2S contains a session JWT + client IP** — private repo, throwaway test character, JWTs expire. Keep `settings.json` and anything from the production account strictly out regardless.
6. **`ClassicUO/settings.json` contains live credentials** — never print, commit, or transmit it.
7. Human pacing everywhere: jittered timings, human-length sessions, no 24/7 operation, no captcha-gated resource farming on autopilot.
8. **Never reveal the harness in-game** (user directive 2026-09-28). No injected speech/emotes/messages may reference the harness, testing, automation, AI, or this project. In-game text must be natural and context-appropriate — including live-validation speech tests (use innocuous phrases like "hello").

## Conventions

- Python 3.13 (`C:\Users\chris\AppData\Local\Programs\Python\Python313\python.exe`) for harness code; PowerShell for Windows glue; Java for Ghidra scripts.
- The shell is git-bash-like: forward slashes or quoted paths (backslashes get eaten), no `$_` in inline PowerShell (write `.ps1` files), `timeout` is GNU syntax, `cp` not `copy`.
- Launching the game: `launch_game.ps1` — Outlands.exe elevates (UAC); the user must accept the prompt.
- Ghidra headless: project dir must pre-exist; import path must have no spaces/parens (use the workspace exe copy); see `docs/NOTES.md` for the full invocation.
- Evidence-first: ground every claim in tool output, source, or capture; label inference `[INFERENCE]`. Don't fabricate verification.
- Keep artifacts: scanners, captures, and notes are worth committing; bulk data is not (see Rule 0 exclusions).

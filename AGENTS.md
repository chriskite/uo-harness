# AGENTS.md — standing instructions for agent sessions

You are working on **uo-harness**: an AI agent harness that plays Ultima Online Outlands. Read `README.md` first, `ANTICHEAT.md` before any network-facing work, `docs/PLAN.md` for the current phase, and `docs/NOTES.md` for operational knowledge.

## Rule 0 — Record and commit engineering knowledge (standing order)

**Knowledge about building the harness must be recorded in the repo and pushed before the session ends.** That covers the approach, decisions and their rationale, protocol/file-format/tooling findings, environment gotchas and anti-cheat findings. If the chat died, the repo must still let the next session continue the engineering work.

**Rule 0 does NOT cover the harness's own runtime memory** (user clarification 2026-09-29): game state, what the agent learned while playing (walk/harvest memory, episodes, hazard stats), event logs and captures of its play. That data lives in the local memory database (docs/MEMORY.md), which is gitignored. Commit a capture only when a test or doc cites it as evidence (a fixture).

- New operational facts, environment discoveries, protocol findings, tool gotchas → `docs/NOTES.md` (append to the right section).
- Decisions and their rationale (approach chosen, alternatives rejected) → `docs/PLAN.md`.
- Anti-cheat / detection findings → `ANTICHEAT.md` (with evidence and confidence level; mark inference as such).
- Phase status / done-criteria met → update `docs/PLAN.md` and `README.md` status line.
- Commit with a descriptive message and **push** (`git push` works via the `github.com-uoharness` SSH alias + deploy key). Verify the push succeeds — do not claim it without seeing it.
- Never commit: secrets (`settings.json`, credentials, tokens), the 67 MB exe copy, string dumps, source tarballs, upstream source trees, the Ghidra project (all covered by `.gitignore` — extend it if a new large/regenerable artifact type appears).

## Constraints
1. **Never touch the client process or install dir.** No injection, no patching, no synthetic OS input into its window, no writes under `C:\Program Files (x86)\Ultima Online Outlands`. Work in `C:\Users\chris\uo-harness`.
2. Never commit: secrets (`settings.json`, credentials, tokens), the 67 MB exe copy, string dumps, source tarballs, upstream source trees, the Ghidra project, or harness runtime memory (all covered by `.gitignore`; extend it if a new large/regenerable artifact type appears). Exception: capture bins may be committed** even though decrypted C2S contains a session JWT because this is a private repo and JWTs expire.
3. **`ClassicUO/settings.json` contains live credentials** — never print, commit, or transmit it.
4. **Never reveal the harness in-game** (user directive 2026-09-28). No injected speech/emotes/messages may reference the harness, testing, automation, AI, or this project. In-game text must be natural and context-appropriate — including live-validation speech tests (use innocuous phrases like "hello").

## Conventions

- Python 3.13 (`C:\Users\chris\AppData\Local\Programs\Python\Python313\python.exe`) for harness code; PowerShell for Windows glue; Java for Ghidra scripts.
- The shell is git-bash-like: forward slashes or quoted paths (backslashes get eaten), no `$_` in inline PowerShell (write `.ps1` files), `timeout` is GNU syntax, `cp` not `copy`.
- Launching the game: `launch_game.ps1` — Outlands.exe elevates (UAC); the user must accept the prompt.
- Ghidra headless: project dir must pre-exist; import path must have no spaces/parens (use the workspace exe copy); see `docs/NOTES.md` for the full invocation.
- Evidence-first: ground every claim in tool output, source, or capture; label inference `[INFERENCE]`. Don't fabricate verification.
- Keep artifacts: scanners, notes, and captures that tests or docs cite are worth committing; bulk and runtime data are not (see Rule 0).

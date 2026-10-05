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
1. **Never touch the client process or install dir.** No injection, no patching, no synthetic OS input into its window, no writes under `C:\Program Files (x86)\Ultima Online Outlands`. Work in the repo checkout (laptop `C:\Users\chris\uo-harness`, desktop `C:\Users\Chris Kite\uo-harness`).
2. Never commit: secrets (`settings.json`, credentials, tokens), the 67 MB exe copy, string dumps, source tarballs, upstream source trees, the Ghidra project, or harness runtime memory (all covered by `.gitignore`; extend it if a new large/regenerable artifact type appears). Exception: capture bins may be committed** even though decrypted C2S contains a session JWT because this is a private repo and JWTs expire.
3. **`ClassicUO/settings.json` contains live credentials** — never print, commit, or transmit it.
4. **Never reveal the harness in-game** (user directive 2026-09-28). No injected speech/emotes/messages may reference the harness, testing, automation, AI, or this project. In-game text must be natural and context-appropriate — including live-validation speech tests (use innocuous phrases like "hello").

## Conventions

- Python 3.13 for harness code; PowerShell for Windows glue; Java for Ghidra scripts.
  - **Run it as plain `python`** (`python harness/foo.py`, `python -m pytest harness/test_x.py`, `python -c "..."`). `python` / `python.exe` is on PATH and resolves to the 3.13 install. Do **not** spell out the interpreter's full path; it costs tokens on every call and adds nothing. `python3` is a broken Microsoft Store stub here, so never use it.
  - **Never hard-code the repo path, the home dir or the interpreter path** in code: the two computers have different home dirs (the desktop's contains a space). Derive the repo from the file's own location (`os.path.dirname(os.path.abspath(__file__))`, `$PSScriptRoot`, `%~dp0`, `getSourceFile()` in Ghidra scripts) and run child Pythons with `sys.executable`. PowerShell/cmd glue uses `$env:UO_PY` if set, else `python.exe` from PATH (skipping the `WindowsApps` Store stub). Quote paths.
- **JSON: use `jq.exe` on the command line, not a Python one-off.** The real jq (1.8.2, `winget install jqlang.jq`) is on PATH. **Type `jq.exe`, not `jq`:** in the omp agent shell `jq` is a builtin (jaq 2.3.0, a jq clone) that shadows PATH and errors where jq returns null: `.a.b` with `.a` missing or null stops the whole filter with "cannot use null as iterable (array or object)", so `{pos, two: .equipment.two_handed.name}` prints nothing when nothing is in that hand (2026-10-04). `jq.exe` behaves the same in every shell. Reach for Python only when the job needs harness code (decoding packets, importing `harness.*`) or logic jq can't express.
  - Inputs that are already JSON: `curl -s http://127.0.0.1:8080/api/state | jq.exe ...` (viz), the state port, `logs/session_*.jsonl` (one JSON object per line, so `jq.exe -c 'select(.id=="0x77")' logs/session_X.jsonl` streams it row by row), `harness/data/*.json`, `docs`-cited fixtures.
  - Cheap patterns: `jq.exe -c '.movement.pos'` (one field), `jq.exe -r '.world.mobiles | keys[]'` (list keys), `jq.exe -c '[.events[] | select(.ev=="speech_heard")] | length'` (count), `jq.exe -s 'group_by(.id) | map({id: .[0].id, n: length})' file.jsonl` (histogram over a jsonl file), `jq.exe 'keys'` / `jq.exe '.x | keys'` first to learn the shape before writing a bigger filter.
  - Save big responses to a file (`curl -s URL > /tmp/s.json`) and query it repeatedly rather than refetching or printing it, **on stdin**: `jq.exe -c '.pos' < /tmp/s.json` (jq.exe is a Windows program and can't open `/tmp/...` paths passed as an argument). Keep output small: project fields, use `-c`, slice with `[:N]` or `limit(N; ...)`.
  - Never print `ClassicUO/settings.json`, with jq or otherwise (Constraint 3).
- The shell is git-bash-like: forward slashes or quoted paths (backslashes get eaten), no `$_` in inline PowerShell (write `.ps1` files), `timeout` is GNU syntax, `cp` not `copy`.
- Launching the game: `launch_game.ps1` — Outlands.exe elevates (UAC); the user must accept the prompt.
- Ghidra headless: project dir must pre-exist; import path must have no spaces/parens (use the workspace exe copy); see `docs/NOTES.md` for the full invocation.
- Evidence-first: ground every claim in tool output, source, or capture; label inference `[INFERENCE]`. Don't fabricate verification.
- Keep artifacts: scanners, notes, and captures that tests or docs cite are worth committing; bulk and runtime data are not (see Rule 0).

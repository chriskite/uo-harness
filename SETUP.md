# SETUP.md — setting the harness up on a second computer

Instructions for a coding agent on a second Windows computer (the user's desktop) that must set
the project up from nothing and then take the harness memory store over from the laptop. Read
`AGENTS.md` and `README.md` first; their rules apply here too. In short: never print, copy or
commit `ClassicUO/settings.json`; never touch the client process or write into
`C:\Program Files (x86)\Ultima Online Outlands`; never reveal the harness in-game; ground every
claim in tool output and mark guesses `[INFERENCE]`.

Steps that need the human are marked **(user)**: UAC prompts, the launcher's 2FA, GitHub.

## 1. Prerequisites

- **Git** (the laptop has `C:\Program Files\Git\cmd`, README "Toolchain").
- **Python 3.13 as plain `python`**, installed per-user with "Add to PATH" (the desktop:
  `winget install --id Python.Python.3.13 --scope user`, 3.13.15, which prepends its dirs to the
  user PATH ahead of `WindowsApps`). Check
  `python -c "import sys; print(sys.version, sys.executable)"` in a **new** terminal (an already
  open shell keeps its old PATH, NOTES "Windows shell / tooling gotchas"). `python3` is a broken
  Store stub; never use it. Nothing hard-codes the interpreter: `ctl.cmd`, `start_proxy_nat.ps1`,
  `restart_divert.ps1` and `register_backup_task.ps1` (`pythonw.exe` beside it) use `UO_PY` if
  set, else `python.exe` from PATH (skipping the Store stub); Python code uses `sys.executable`.
- **jq** (on the laptop it is jaq 2.3.0, AGENTS.md "Conventions"). [INFERENCE] `winget` can
  install either; NOTES "Windows shell / tooling gotchas" lists winget's quirks (UAC-blocking
  installers, slow installs hitting the 900 s job timeout).
- **Bun** for the visualizer frontend (`viz/package.json` scripts run `bun build.ts`,
  `bun test`, `tsc --noEmit`).
- **The game (user):** install Ultima Online Outlands to the default
  `C:\Program Files (x86)\Ultima Online Outlands` with the official launcher (`Outlands.exe`,
  elevates). The first launcher login on a new device needs email 2FA (NOTES "Login &
  identity"); the user does that. The launcher writes `ClassicUO/settings.json` with the
  credentials; do not open, print, copy it from the laptop, or edit it.
- Client settings the harness assumes (NOTES "Environment & client"): Always Run **on**, Auto
  Open Doors **off**. The user sets these in the client.
- The shell is git-bash-like: forward slashes or quoted paths, no `$_` in inline PowerShell,
  `cp` not `copy`, `.cmd` files run (`./ctl.cmd status`), shebang scripts don't (NOTES
  "Windows shell / tooling gotchas").

## 2. Repo access

The repo is `chriskite/uo-harness` (private). On the laptop the remote is
`git@github.com-uoharness:chriskite/uo-harness.git`: an SSH host alias `github.com-uoharness` in
`~/.ssh/config` that pins the deploy key `~/.ssh/uo_harness_deploy` (NOTES "Windows shell /
tooling gotchas"). Give the desktop its own key; don't copy the laptop's.

1. Generate a key (note `-N ''`; in this shell `-N '""'` sets a literal `""` passphrase):
   `ssh-keygen -t ed25519 -N '' -C "uo-harness desktop" -f ~/.ssh/uo_harness_deploy`
2. Add the alias to `~/.ssh/config` (the laptop's block, verbatim):
   ```
   Host github.com-uoharness
       HostName github.com
       User git
       IdentityFile ~/.ssh/uo_harness_deploy
       IdentitiesOnly yes
   ```
3. **(user)** Print the public key (`cat ~/.ssh/uo_harness_deploy.pub`, public half only) and
   have the user add it on GitHub: repo `chriskite/uo-harness` → Settings → Deploy keys → Add
   deploy key, with **Allow write access** ticked (agents push per AGENTS.md Rule 0). A deploy
   key can be registered on one repo only, hence a new key.
4. Check: `ssh -T git@github.com-uoharness` (GitHub greets and exits 1).
5. Clone anywhere (the desktop: `C:/Users/Chris Kite/uo-harness`; the laptop:
   `C:/Users/chris/uo-harness`). Code derives the repo from its own location, so the path may
   differ and may contain spaces (exception: Ghidra's `-import` path, NOTES "Ghidra headless
   gotchas"):
   `git clone git@github.com-uoharness:chriskite/uo-harness.git`

   The desktop instead was cloned with the user's own GitHub key (`~/.ssh/id_ed25519`, remote
   `git@github.com:chriskite/uo-harness`, `ssh -T git@github.com` greets `chriskite`), which
   can push, so steps 1–4 were skipped there.

Not in git (`.gitignore`) and what to do about it:

| Path | Needed for | On the desktop |
|---|---|---|
| `harness/data/harness.db` (+`-wal`/`-shm`) | memory store | §7 handoff |
| `logs/` | session captures, task logs | `dbhandoff.py push/pull` copies them (§7) |
| `harness/data/telegram.json` | Telegram bridge (bot token) | `dbhandoff.py pull` installs it (§7) |
| `models/laya-triage/` | the deployed Laya triage model (`triage.py serve`), 843 MB | `dbhandoff.py pull` installs it (§7) |
| `harness/data/discord*.db`, `discord_media/`, `discord_profile/` | Discord tooling | §5, stays on one computer |
| `.venv-laya/`, `.venv-discord/` | optional services | §5 |
| `viz/node_modules/`, `viz/dist/` | visualizer | `bun install && bun run build` (§4) |
| `windivert.zip`, `WinDivert-2.2.2-A/` | nothing at runtime | skip; the driver files the NAT uses (`harness/WinDivert.dll`, `harness/WinDivert64.sys`) are committed |
| `ClassicUO.exe` copy, `ghidra/`, `ClassicUO-main/`, `Razor-master/`, `strings.txt`, `decompiled/`, `ref/` | reverse-engineering only | skip unless that work moves here; README "Not committed" has the codeload commands, NOTES "Ghidra headless gotchas" the Ghidra invocation |

## 3. Python dependencies and offline tests

The core harness is mostly stdlib. Third-party imports found by grep over `harness/`:

| Package | Used by | Install |
|---|---|---|
| `pydivert` | `harness/divert_nat.py` (the NAT) | `python -m pip install pydivert==3.1.3` (the laptop's version) |
| `numpy` | `harness/knowledge.py`, `harness/embedder.py` (top-level), `harness/lumber_opt.py` (lazy) | from the GPU stack line below |
| `fastembed-gpu`, `onnxruntime-gpu`, `nvidia-*` | `harness/embedder.py` (`ctl know search`/`brief`) | NOTES "Discord capture" → "System Python has the GPU stack too": `python -m pip install "fastembed-gpu==0.8.1" "onnxruntime-gpu==1.30.0" "nvidia-cuda-runtime==13.4.92" "nvidia-cublas==13.8.0.4" "nvidia-cufft==12.4.0.43" "nvidia-cudnn-cu13==9.27.0.42" "numpy==2.5.3"`. Never also install CPU `fastembed`/`onnxruntime`. Without CUDA it falls back to CPU; `UO_EMBED_CPU=1` forces CPU |
| `windows-capture` (pulls numpy, opencv-python) | `harness/screen.py`, `harness/liveview.py` (`ctl screenshot`, live view) | `python -m pip install --user windows-capture` (NOTES "Windows shell / tooling gotchas"; laptop has 2.0.1) |

[INFERENCE] the GPU stack assumes an NVIDIA GPU with a driver supporting CUDA 13 (the laptop:
RTX 5070 Laptop, driver 595.97). On another GPU check `ctl know search` reports `recall: hybrid
(cuda)`; CPU fallback still works.

Offline tests: the documented list is HANDOFF.md "Operate" → "Tests". Each test is a script run
with plain `python`; they use private ports and are safe while a live proxy runs. A quick set:

```
python harness/test_memory.py
python harness/test_world.py
python harness/test_world_replay.py
python harness/test_actions.py
python harness/test_nav.py
python harness/test_viz.py
python harness/test_agent_gate.py
python harness/test_facet.py
python harness/test_backup.py
python harness/test_dbhandoff.py
python test_proxy.py
python test_movement.py
python test_errand.py
```

`python test_loop_lumber.py` takes ~1.5 min. `harness/test_*.py` has more (ctl, knowledge,
telegram bridge, ...). `harness/test_facet.py` and the viz terrain read `facet00.mul` from the
install dir, so they need the game installed [INFERENCE: not checked whether test_facet skips
without it]. Tests that need the Discord venv (`test_discord_*`) are optional (§5).

## 4. Interception, game, proxy, viz, overseer

All procedures live in the docs; follow them as written.

- **All services at once:** `python harness/stack.py up` in a console starts and supervises the
  proxy, the viz, laya and the Telegram bridge, and starts the divert NAT through
  `start_proxy_nat.ps1` (**(user)** accepts the UAC prompt). Same rule as below: only once this
  computer holds the store (§7). `python harness/stack.py down` before a handoff push.
  Details: docs/NOTES.md "Stack supervisor". The items below are the pieces it runs.

- **Proxy + NAT:** `powershell -ExecutionPolicy Bypass -File start_proxy_nat.ps1` (run
  non-elevated; **(user)** accepts one UAC prompt for the NAT). The proxy opens its own console
  (Ctrl-C there flushes the memory store); the NAT runs in a minimized elevated window and logs
  to `divert.log`. Details, manual steps and gotchas: `docs/INTERCEPTION.md` "Operation runbook".
  Because of the store handoff, start the proxy only once this computer holds the store (§7).
- **Game:** `powershell -Verb RunAs launch_game.ps1`; **(user)** accepts UAC and logs in.
  Elevation is required (NOTES / INTERCEPTION.md).
- **Visualizer:** `cd viz && bun install && bun run build` once, then
  `python harness/viz_server.py --live` → http://127.0.0.1:8080/ (HANDOFF.md "Operate",
  `docs/VISUALIZER.md`). LAN access from other devices: `allow_viz_lan.ps1` from an
  **administrator** PowerShell (it refuses otherwise) and `viz_server.py --host 0.0.0.0`.
- **Overseer:** `./ctl.cmd status` must return `ok:true`; then start an omp session as in
  `docs/OVERSEER.md` §6. `ctl` reference: §2.
- **Telegram bridge:** `docs/OVERSEER.md` §8. `dbhandoff.py pull` installs the laptop's
  `harness/data/telegram.json` (bot token, paired chat; §7), so no new pairing is needed. It's a
  secret: never print it. Run only **one** bridge per bot at a time (a second poller gets 409
  Conflict). `push` refuses while the bridge runs, so the laptop's is already stopped.
- Other runtime commands (agent gate, lumber loop, memory, errands): HANDOFF.md "Operate".

## 5. Optional components

- **Laya speech triage** (`harness/triage.py`; without it speech holds just carry no Laya
  verdict). NOTES "Laya speech triage":
  `py -3.13 -m venv .venv-laya && .venv-laya/Scripts/python.exe -m pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cu130 && .venv-laya/Scripts/python.exe -m pip install "laya[serve]"`
  The cu130 index is for the laptop's sm_120 GPU; check the desktop GPU's arch first. Run with
  `python harness/triage.py serve`; `python harness/triage.py health` must report
  `"device": "cuda"`. Checkpoints download (~2.4 GB) on first use.
- **Discord capture / search / KB.** NOTES "Discord capture":
  `py -3.13 -m venv .venv-discord && .venv-discord/Scripts/python.exe -m pip install patchright zstandard fastembed-gpu "nvidia-cuda-runtime==13.*" "nvidia-cublas==13.*" nvidia-cufft nvidia-cudnn-cu13`
  Needs Microsoft Edge. Its data (`discord.db`, `discord_kb.db`, `discord_media/`, the login
  profile) is **not** part of the store handoff: run the Discord tooling on one computer only.

## 6. Backups

Register the hourly backup task on the desktop too:
`powershell -ExecutionPolicy Bypass -File register_backup_task.ps1` (non-elevated; runs
`harness/backup.py` as the current user while logged on). It copies to the NAS share
`\\STARGAZER\files\uo-harness` by UNC path (NOTES "Backups"). Check the share is reachable from
this user's logon session [INFERENCE: the desktop may need the share's credentials once,
**(user)**]. Status: `logs/backup.log`, `\\STARGAZER\files\uo-harness\last_backup.json`.

`backup.py` skips the `harness.db` snapshot on a computer that doesn't hold the store, so a
laptop waking from sleep can't upload a stale snapshot as the newest backup. Both computers keep
the task registered.

## 7. Taking the memory store over

`harness/data/harness.db` is the harness's durable memory (docs/MEMORY.md). Exactly one computer
holds it at a time; `harness/dbhandoff.py` moves it through the NAS share. Operational details:
`docs/NOTES.md` "Two computers (memory store handoff)".

```
python harness/dbhandoff.py status|push|pull [--dest PATH]
```

Default `--dest` is `\\STARGAZER\files\uo-harness`; snapshots go to
`<dest>/handoff/harness-genNNNN.db.gz`, the holder and generation to `<dest>/handoff/owner.json`.
Local state: `harness/data/handoff.json` (gitignored).

**Laptop → desktop:**

1. On the **laptop**: stop everything that has `harness.db` open: the proxy (Ctrl-C in its
   window, so it flushes), runners and `ctl run` tasks (`ctl stop`), the overseer session, the
   Telegram bridge, the viz server, any other Python with `Memory` open.
2. On the laptop: `python harness/dbhandoff.py push`. It checkpoints the store, uploads a
   verified gzipped snapshot, bumps the generation and releases the store; it also copies
   `logs/` to `<dest>/logs` (`--no-logs` skips that). The very first push (no
   `<dest>/handoff/owner.json` yet) creates generation 1 from the laptop's store. The desktop
   agent can't run this step; the user (or the laptop's agent) does.
3. On the **desktop**: `python harness/dbhandoff.py status` (expect: no holder, a pull would
   install the latest generation).
4. On the desktop: `python harness/dbhandoff.py pull`. It downloads and verifies the snapshot,
   keeps any replaced local store as `harness/data/harness.db.prev`, installs the new one,
   records this computer as holder and copies `<dest>/logs` into `logs/` (newer files only;
   `--no-logs` skips). On a fresh desktop with no `harness.db` it just installs the snapshot.
   It also installs the pushed `harness/data/telegram.json`; a different local one is kept as
   `telegram.json.prev`. The output says `"telegram": "installed"` or `"unchanged"`. And it
   installs the Laya triage checkpoint `models/laya-triage/` (843 MB, verified per file; a
   different local one is kept as `models/laya-triage.prev`): `"model": "installed"` or
   `"unchanged"`. If laya-serve is running, stop it first, or the pull refuses before changing
   anything.
5. `python harness/dbhandoff.py status` now shows the desktop as holder. Start the proxy etc.

**Desktop → laptop** is the mirror image: stop all writers on the desktop, `push` there,
`status` and `pull` on the laptop.

**Refusals and what to do:**

| Refusal | Cause | Do |
|---|---|---|
| `push`: store is open | a writer still runs | stop it (step 1) and push again |
| `pull`: another computer holds the store | it hasn't pushed | push on that computer first |
| `pull`: local store changed since this computer's last push/pull | fork: both computers played from the same generation | the user decides which history to keep. `pull --discard-local` takes the remote one; the local copy survives as `harness/data/harness.db.prev`. To keep the local one instead, `push --force` from here (overwrites the remote generation) |
| first `pull` on a new computer: "this store has unsynced changes" | a harness command run before the pull created an empty `harness.db` (NOTES "Two computers") | if `python harness/memory.py stats` shows all zeros, `pull --discard-local` |
| `push`: this computer isn't the holder / its copy isn't the latest generation | stale copy | normally pull first. `push --force` overwrites; recovery only |
| holder is dead (disk gone, can't push) | | `pull --force` on the other computer; it gets the dead one's last push |

**What doesn't travel with the handoff:** `discord.db`, `discord_kb.db`, `discord_media/`
(run the Discord tooling on one computer only); ClassicUO `settings.json` (each computer logs
in through the launcher itself; never copy or print it); the venvs; the Laya training data
(`harness/data/triage/`). `telegram.json` and the Laya checkpoint do travel (see step 4); the
bot token is therefore stored on the NAS share.

## 8. Checklist

- [ ] `python --version` is 3.13 in a fresh terminal, and `sys.executable` is not under `WindowsApps`.
- [ ] `git fetch` works in the checkout.
- [ ] Offline tests from §3 pass; `cd viz && bun test && bun run typecheck` passes.
- [ ] `python harness/dbhandoff.py status` shows this computer as holder at the latest generation.
- [ ] `python harness/memory.py stats` row counts match the laptop's output from just before its push.
- [ ] Proxy and NAT up (`start_proxy_nat.ps1`), game launched and logged in by the user, `./ctl.cmd status` returns `ok:true`.
- [ ] `python harness/viz_server.py --live` serves http://127.0.0.1:8080/ and shows the character.
- [ ] Backup task registered; after its first run `logs/backup.log` ends with `OK`.
- [ ] Only one computer runs the proxy, the Telegram bridge and the Discord tooling.

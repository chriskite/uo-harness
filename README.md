# uo-harness

An AI agent harness that plays **Ultima Online Outlands**.

Current status: **Phases 1 and 3 done; Phase 2 (world model) re-validated offline after an S2C decode correction; Phase 4 (agent runtime) under way: the lumberjack loop (harvest → boards → inn rental room storage, no deeds) ran end to end live on the Test Shard on 2026-09-29** ([`docs/LUMBER_LOOP.md`](docs/LUMBER_LOOP.md) §13). **Since 2026-10-01 the loop banks the boards instead (walk to the banker, say `bank`, drop them into the bank box) on Shelter with a fresh Young character; offline-proven, not yet run live** (LUMBER_LOOP.md §12.5). Phase 3's acceptance task, an unattended bank run (walk to the banker, open the bank box, walk back), completed live on the Test Shard on 2026-09-29. The proxy runs the world model live (state port), records durable harness memory ([`docs/MEMORY.md`](docs/MEMORY.md)), hides agent walk confirms from the client and re-anchors it client-side, and never sends anything of its own to the server. See [`HANDOFF.md`](HANDOFF.md), [`docs/MOVEMENT.md`](docs/MOVEMENT.md), [`docs/CIPHER.md`](docs/CIPHER.md). Read [`ANTICHEAT.md`](ANTICHEAT.md) first — it defines the safety constraints every design decision follows.

**Since 2026-10-02 the lumber job optimizes itself** (logs/hour as the proxy for gold/hour): `ctl lumber plan` picks the next spot by Thompson sampling over every recorded trip, the trip size from each spot's learned death, sent-home and theft hazards and walking overhead (a renewal-reward rate since 2026-10-04: a death banks nothing and loses every unblessed item carried), the tree regrowth window from harvest memory, and the hatchet from tool bonus, wear and the loss on death, rescaling old data to today's skill ([`docs/LUMBER_LOOP.md`](docs/LUMBER_LOOP.md) §6, `harness/lumber_opt.py`; overseer procedure in [`docs/OVERSEER.md`](docs/OVERSEER.md) §5). **Since 2026-10-03 it reaches the whole map:** spots near the ~360 Witcher runes are recalled to from the public Cambria rune library and left by our own runebook ([`docs/research/WORLD_LOCATIONS.md`](docs/research/WORLD_LOCATIONS.md)), failed spots are set aside by evidence, and the overseer's walks route around hostile creatures (`harness/travel_guard.py`). **Since 2026-10-04 it chops by Smart Harvest** (the hatchet's cursor answered with ourselves, as the stock client does; the runner picks where to stand, the server picks the tree): built and proven offline, the attended live trip that measures its reach is pending (docs/PLAN.md "Smart Harvest for lumber").

**Since 2026-10-03 the captured Outlands Discord feeds a vetted knowledge base:** `harness/discord_kb.py` extracts grounded claims with Sonnet, clusters and adjudicates them into facts with deterministic verdict rules (official / consensus / single-source / disputed), promotes official and consensus facts into the overseer's `ctl know` store (tag `discord`, source `community`/`doc`), and writes the digest [`docs/research/DISCORD_KB.md`](docs/research/DISCORD_KB.md) for coding agents (docs/PLAN.md "Discord knowledge base").

**Since 2026-10-04 the harness knows two rune libraries:** the public Cambria one (Witcher set) and the DTF guild house's 36 tomes (839 runes: a Witcher set plus towns, docks, dungeons and POIs), each rune with the tile it lands on, read off the tomes by `ctl act read_tomes` without recalling. `ctl runes near X Y` / `runes find WORDS` name the rune for a destination; `ctl act recall --library dtf --rune NAME` or `--witcher N` uses it; lumber trips recall out from the nearest library that holds their rune ([`docs/research/WORLD_LOCATIONS.md`](docs/research/WORLD_LOCATIONS.md) §5).

Setting the harness up on another computer, and moving the memory store between computers: [`SETUP.md`](SETUP.md).

## Architecture (decided)

```mermaid
flowchart LR
  subgraph Client
    CUO[Outlands ClassicUO.exe<br/>stock, untouched]
  end
  subgraph Harness[Agent Harness - Python]
    PX[Rewriting TCP proxy<br/>localhost:2593 + Huffman codec<br/>+ agent injection]
    WM[World model<br/>entities / gumps / journal / stats]
    AG[Agent runtime<br/>LLM planner + skills]
  end
  CUO <-->|UO protocol, via WinDivert NAT| PX
  PX <-->|client traffic + agent packets,<br/>walk seq/key on one ladder| SRV[game server :2593<br/>IP chosen per login]
  PX --> WM --> AG
  AG -->|action packets| PX
```

- **Localhost proxy** between the stock client and the game server; the client reaches it through a WinDivert NAT ([`docs/INTERCEPTION.md`](docs/INTERCEPTION.md)), `settings.json` is untouched. No client modification, no synthetic OS input.
- **What the server sees:** the client's own traffic plus the agent's injected packets, each built in the stock client's shape (layouts from the client's own packet table, companion packets and ordering as the client sends them). Walks from both senders share one seq/fastwalk-key ladder (docs/MOVEMENT.md). The proxy never originates a packet of its own; it drops one kind of client packet: a reply to a target cursor the agent already answered (ANTICHEAT.md §8.18).
- **What only the client sees:** agent walk confirms are hidden; the proxy hands the client fabricated 0x21 re-anchors, gump closes and target cancels so its screen matches the server (ANTICHEAT.md §8.11, §8.18).
- Protocol ground truth: upstream ClassicUO source (`ClassicUO-main/` locally, not committed; fetch via codeload — see below).
- See [`docs/PLAN.md`](docs/PLAN.md) for phases and rejected alternatives, [`docs/NOTES.md`](docs/NOTES.md) for the operational knowledge base.

## Hard facts (verified 2026-09-27)

| Fact | Value |
|---|---|
| Client | `ClassicUO.exe` STANDARD_BUILD **1.0.2.544** at analysis time (2026-09-27); the launcher patched it to **1.0.2.550** on 2026-09-28 (JWT `version` claim). NativeAOT native binary (no IL, no injection surface) |
| Launcher | `Outlands.exe` — patcher + OutlandsID login UI, elevates (UAC), `-installed` arg |
| Install dir | `C:\Program Files (x86)\Ultima Online Outlands` — **never write into it** |
| Game server | literal IP from the HTTPS login response, varying by login: 74.91.115.123 (Test Shard), 35.71.142.123 and 52.223.17.219 (2026-09-30), all on :2593. The NAT diverts any IP on :2593 (`play.uooutlands.com` does not resolve) |
| Auth | `https://login.uooutlands.com` (Cloudflare), JWT with `uooutlands.com/identity/claims/*` |
| Runtime net surface | exactly 2 connections: short HTTPS auth + persistent game TCP. No telemetry/beacons |
| Test account | shard "Test Server", character `TestWorth` (settings.json) |
| Assistant | Razor CE fork compiled into client; scripts dir `ClassicUO/Data/Plugins/Assistant/Scripts/` |

## Safety doctrine (summary — full version in ANTICHEAT.md §8)

1. Never touch the client process or files. 2. The proxy never originates server-visible packets; `Send_TimeSyncPingReq`/keepalives pass through unaltered; agent packets are stock-shaped. 3. Human pacing with jitter, human-length sessions. 4. Honor Razor-gating signals (halt when server restricts assistants). 5. No DeviceId/TPM/2FA spoofing; log in via official launcher. 6. No writes to the install dir. 7. CAPTCHAs are human-solved by default (the runner pauses and beeps); the viz header's `captcha [human|auto]` toggle switches to auto-solve from the gump layout (`harness/captcha.py`, margin-gated, falling back to the human).

## Repo layout

| Path | Content |
|---|---|
| `ANTICHEAT.md` | Anti-cheat/automation-detection research report (the core document) |
| `docs/PLAN.md` | Build plan: phases, deliverables, rejected alternatives |
| `docs/NOTES.md` | Operational knowledge base (environment, gotchas, protocol nuggets) |
| `extract_strings.py` | ASCII+UTF-16 string extractor for native binaries |
| `scan_ac.py` | Categorized anti-cheat string scanner + context dumper |
| `scan_endpoints.py` | Network endpoint extractor (URLs/hosts/paths) |
| `scan_launcher.py` | Launcher fingerprint + API surface extractor |
| `ghidra_scripts/ACXrefs.java` | Ghidra headless xref script for flagged strings |
| `monitor_endpoints.ps1` | Per-process TCP endpoint monitor (behavioral capture) |
| `launch_game.ps1` | Game launcher helper (handles elevation) |
| `api_surface.txt`, `endpoints.txt`, `grep_hits.txt`, `launcher_hits.txt`, `behavioral_endpoints.csv` | Evidence artifacts |

## Not committed (regenerable / third-party)

- `ClassicUO.exe` copy, `strings.txt`, `launcher_strings.txt` — regenerate with `extract_strings.py`
- `ghidra/` — Ghidra project (re-run headless import; see NOTES.md for invocation gotchas)
- `ClassicUO-main/`, `Razor-master/` — upstream source trees:
  - `curl -sL https://codeload.github.com/ClassicUO/ClassicUO/tar.gz/refs/heads/main | tar xz`
  - `curl -sL https://codeload.github.com/markdwags/Razor/tar.gz/refs/heads/master | tar xz`

## Toolchain

Python 3.13 per-user install, plain `python` on PATH · Git `C:\Program Files\Git\cmd` · Bun 1.4.2 `~\.bun\bin` · Ghidra `~\ghidra_12.1.4_PUBLIC` (laptop only) · Java 25 (Temurin) · Wireshark `C:\Program Files\Wireshark` (laptop) · git push via SSH host alias `github.com-uoharness` (deploy key `~/.ssh/uo_harness_deploy`, laptop) or the user's own GitHub key (desktop). Second computer: SETUP.md.

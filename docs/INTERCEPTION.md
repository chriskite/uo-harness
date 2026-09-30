# Traffic interception architecture — how the client lands on the proxy

Status: **WORKING LIVE** (2026-09-28, TestWorth in-game through the full chain).

## Why every simpler approach fails

| Approach | Why it fails |
|---|---|
| `-ip`/`-port` CLI args | Outlands fork ignores them for the game connection. |
| `settings.json` `ip`/`port` | Also ignored: the game server address comes from the HTTPS login response (`ServerInfo.Ip`) as a **literal IP** (74.91.115.123). |
| `hosts` file / local DNS | `play.uooutlands.com` does not exist in DNS at all (NXDOMAIN even via 8.8.8.8) — the client never resolves a name. |

## The chain

```
ClassicUO.exe ──TCP to 74.91.115.123:2593──> [WinDivert NAT] ──loopback──> [harness proxy :2593] ──> 74.91.115.123:2593
```

1. **`harness/divert_nat.py`** (elevated; WinDivert 2.2.2 driver, files in `harness/`):
   - **forward rule**: `outbound and dst==74.91.115.123:2593 and srcport∉[25940..25960]` → rewrite src AND dst to `127.0.0.1` (dst port unchanged).
   - **return rule**: `src==127.0.0.1:2593` → rewrite src to `74.91.115.123:2593`, dst back to the client's real IP (learned from forward packets).
   - **Both legs must be full loopback**: rewriting only the destination gets the packet martian-dropped by the Windows stack (loopback dst + non-loopback src = dropped). src+dst rewrite makes it genuine loopback traffic.
   - Return-path loopback packets ARE visible to WinDivert 2.2 on Win11 (verified; `loopback=True` in logs).
2. **`harness/proxy.py`** on `127.0.0.1:2593`: relay + decode. Since Phase 3 it also injects agent packets, rewrites every walk's seq/key onto one ladder, hides agent walk confirms from the client and fabricates client-only packets (docs/MOVEMENT.md, ANTICHEAT.md §8.11, §8.18). Upstream leg binds to source ports 25940–25960 (retry loop over the range on bind failure — fixed single port hits TIME_WAIT, WinError 52) and the divert excludes that range to prevent loops.
3. Client launch: `launch_game.ps1` (**elevation required** — the client opens game files in a way that needs write access to the install dir; non-elevated launch crashes in `UOFileManager.Load` with `animdata.def` access denied). `settings.json` is left untouched (edits get overwritten and are unnecessary with the NAT).

## Operation runbook

1. Start proxy: `python harness/proxy.py --upstream-host 74.91.115.123 --upstream-bind 0.0.0.0 --upstream-bind-port 25940 --logdir logs`
2. Start NAT (elevated): `powershell -Verb RunAs restart_divert.ps1` (kills old divert_nat instances, starts fresh, logs to `divert.log`)
3. Launch client (elevated): `powershell -Verb RunAs launch_game.ps1` — log in normally.
4. Session lands in `logs/session_<ts>.jsonl` (+ `.c2s.raw`/`.s2c.raw`).

## Gotchas learned

- **WinError 52 "duplicate name"** = upstream bind port in TIME_WAIT; use a port range + retry loop.
- Elevated processes can't be killed from a medium-integrity shell — kill them from an elevated script (`restart_divert.ps1` pattern).
- `netsh int ip add address` on a DHCP interface flips it to static and **kills the lease** — don't alias NICs this way; the port-based exclusion made the alias unnecessary.
- `Set-Content -Encoding UTF8` writes a BOM — PowerShell 5.1 gotcha when touching JSON configs.
- pktmon captures duplicate every packet on Wi-Fi (dedupe by TCP seq when reassembling).

## Detection notes (for ANTICHEAT.md)

- The NAT is kernel-level packet rewriting on the local machine; the client process is untouched, its files are stock, and the server sees the traffic from the same IP (source port differs from a direct connection — indistinguishable from NAT/router behavior which every internet connection traverses anyway). What the proxy changes in that traffic is covered in ANTICHEAT.md §8 and §10; the NAT itself changes no payload byte.
- WinDivert loads a signed third-party driver (`WinDivert64.sys`). It does not interact with the game process.

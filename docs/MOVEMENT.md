# Movement (walk) acceptance — Outlands (2026-09-28)

How to build a `0x02` walk packet the Outlands server will *execute* (step), not
just *acknowledge* (turn). Every claim is tagged: **[CODE]** = decompiled client
binary (Ghidra, `decompiled/protocol_handlers.c` + targeted headless decompiles),
**[UPSTREAM]** = ClassicUO-main / RunUO source, **[CAPTURE]** = session logs,
**[INFERENCE]** = reasoned from the previous three, marked where used.

Reference implementation + replay validation: `harness/movement_driver.py`
(run `python harness/movement_driver.py` — mirrors 289 client walks from all
four sessions byte-for-byte).

---

## 1. Wire format (proven)

C2S walk = 7 bytes, XOR session key (applied by the proxy):

```
02 <dir|0x80 if run> <seq u8> <fastwalk-key u32be>
```

**[CODE]** `NetClientExt.Send_WalkRequest @ 0x140179c60`: fixed-length packet
(`PacketsTable.GetPacketLength(2)` ≥ 0 → 7B); writes `02`, `dir | 0x80` when the
run arg is set, `seq` (2nd arg), key (5th arg) big-endian. **[CAPTURE]**
e.g. `02 82 00 00000008` (run, dir 2, seq 0, key 8), `02 00 04 00000000`.

## 2. The sequence byte — rules

Client state: `WalkerManager+0x2f` (`WalkSequence`), per `PlayerMobile.Walk
@ 0x140328440` **[CODE]**:

- starts at `0` (new `WalkerManager` per login),
- sent in the walk, then post-incremented: `if (seq == 0xFF) seq = 1 else seq++`
  (**wraps 0xFF → 1, never 0** — matches upstream `PlayerMobile.cs:673-680`;
  the harness `WalkSequencer` doc comment saying "wraps 255->0" is WRONG),
- **reset to 0 only by**: (a) S2C `0x21` DenyWalk handler
  (`WalkerManager.Reset @ 0x14030b130`, zeroes seq + steps + timers + flags),
  (b) S2C `0xBF` subcommand `0x0001` (fastwalk seed → calls
  `PacketHandlers.OnPlayerTeleport @ 0x140190500` → `ClearSteps` + `Reset`),
  (c) the legacy (non-V10) S2C `0x20` MobileUpdate about the player (not used by
  Outlands; the 28-byte V10 handler `MobileUpdateV10 @ 0x140188d10` does NOT reset).

Notably **idle time does not reset seq**, and **the client resync
(C2S `22 0000`) does not reset the client's seq** (the `ConfirmWalk` bad-step
path only zeroes `StepsCount`/`CurrentWalkSequence`, not `WalkSequence`)
**[CODE]** **[CAPTURE]** session_20260928_164548: 165 walks, seq `00..0xa4`
continuous across 45 s idles; session_20260928_223537: seq `00..03`, 38 s idle,
continues `04..08`.

Server side (RunUO lineage, `ref/runuo_PacketHandlers.cs:1571` **[UPSTREAM]**):

```csharp
if ((state.Sequence == 0 && seq != 0) || !m.Move(dir)) {
    state.Send(new MovementRej(seq, m));   // 0x21 deny
    state.Sequence = 0;
    m.ClearFastwalkStack();
} else { state.Sequence = (seq + 1 == 256) ? 1 : seq + 1; }
```

and `Resynchronize` (C2S `0x22`) sets `state.Sequence = 0` (ibid. :1354).

**Acceptance rule:** next walk's seq must equal the server's expected value
(`last+1`, wrap `0xFF→1`); after login / resync / deny / teleport-seed the
expectation is `0`.

## 3. The key field — the movement token (Q1 answer)

The last u32 is the classic **fastwalk key**, but on Outlands it is *alive*
(stock RunUO/ServUO never validate it — the field is read and ignored, and
stock servers never send the key packets; verified in
`ref/runuo_PacketHandlers.cs:1575`, `ref/runuo_Mobile.cs:3075`,
`ref/servuo_PacketHandlers.cs:1658` **[UPSTREAM]**).

Client sourcing **[CODE]** — the key is *only* ever a server-issued token:

- `PlayerMobile.Walk` passes `FastWalkStack.GetValue()` (`FUN_14030ada0 @
  0x14030ada0`) as the key: **pop the first non-zero of 5 slots, zero it,
  return it; 0 if all empty** (5 slots, not upstream's 6 — `AddValue` scans
  indices 0..4 only).
- The stack is fed **exclusively** by S2C `0xBF` extended commands
  (`PacketHandlers.ExtendedCommand @ 0x140194bc0`; xref on
  `FastWalkStack.AddValue @ 0x14030ad40` shows no other caller; the array is
  only directly written in `case 1`):
  - `BF 001d 0001 <6×u32be>` — seed: stores slots 0..4 (6th key read but
    dropped), then calls `OnPlayerTeleport` → **walker reset (seq = 0)**,
  - `BF 0009 0002 <u32be>` — push one key into the first empty slot, no reset.

Observed behavior **[CAPTURE]**:

- Exactly **one non-zero key per "arming event"**: keyed walks are always
  isolated; all continuation walks carry `00000000`. (If a 6-key seed with
  all-nonzero keys ever arrived, 5 consecutive walks would be keyed — never
  observed.)
- Keyed walks appear (a) on the first walk after login and (b) on the first
  walk of a movement burst after long idles, and (c) once mid-burst after a
  38 s idle with *continuing* seq (session_20260928_223537 t=3394.209,
  `02 84 04 00000001`) — i.e. case (c) was a `BF sub2` push (no seq reset),
  cases (a)/(b) are consistent with `BF sub1` (reset + seed, seq restarts at 0)
  or `sub2`.
- Values are small (`8`, `1` — 8,8,1,8,8,1 across sessions), i.e. a server-side
  counter/nonce, not random u32.
- **The proxy's S2C decode never surfaced a `0xBF` frame** (searched the raw
  decoded streams of all four sessions for `bf 001d 0001` / `bf 0009 0002` —
  zero hits), yet the client provably received keys. The S2C capture is lossy
  (635 desync events in session_20260928_211622; session_20260928_141253 has
  only 103 framed S2C rows in 443 s). The key-push packets are being swallowed
  by the desynced regions of the custom-dialect stream. **[INFERENCE]** This,
  not a client-side key generator, explains their invisibility: the client code
  has *no other* key source.

**When must the key be non-zero?** Whenever the server has an **outstanding
(unconsumed) token**: after every `BF sub1/sub2` push, the *next* walk must
carry that token (it is popped and consumed). When no token is outstanding,
`00000000` is correct — all observed continuation walks carry 0 and execute.

## 4. Why injected walks turn but never step (Q3, the core)

Observed: injected walks — byte-wellformed `02 <dir> <seq> <key>` — make the
character **turn** (server processes direction) but **never step**; the same
bytes from the real client step fine. The server sends **no** `0x21` deny and
no `0x22` ack for either outcome (genuine `0x21`/`0x22` frames do not appear in
any session; `0x21`/`0x22`-shaped hits are misframes of the custom stream).

Evidence from the captures **[CAPTURE]**:

- session_20260928_223537: five robotic injections `02 82 00 00000000`
  (exactly 1.000 s apart, key 0) — all ignored. 123 s later the client's first
  real walk `02 82 00 00000008` — identical except **key = 8** — executes, then
  continuations with key 0 execute.
- session_20260928_211622: injections = runs at *exactly* 0.250 s / 0.400 s
  intervals, key always 0, seq restarting at 0 per batch (the WalkSequencer).
  Human runs = irregular 0.1–0.8 s intervals, first walk keyed (8,1,8,8,1).
  (Real client auto-walk cadence is 0.200 s — session_20260928_164548 — so the
  0.250/0.400 s metronome runs are not the client.)
- In stock RunUO, `Mobile.Move` applies the direction only at the *end* of a
  successful move **[UPSTREAM]**, while a direction-change-only request
  (facing ≠ requested dir) is a *turn*, which cannot fail. Outlands' server
  visibly applies the direction of rejected walks — i.e. it processes the
  packet, then refuses the step silently.

**The discriminator is the fastwalk token (with seq as a second gate).**
**[INFERENCE — strong; consistent with all evidence, no counter-evidence]**

The server mirrors the token stack it pushes:

1. Server pushes a token (`BF sub1` at login — also resetting both sides' seq;
   `BF sub2` later, e.g. before bursts / after recovery events).
2. While a token is **outstanding**, the next walk **must carry exactly it**.
   A walk with key 0 (or a stale/wrong key) gets *direction-only* processing:
   turn applied, step dropped, no deny. This is Outlands' custom
   anti-speedhack gate — the per-connection movement token.
3. A correct token is consumed (popped both sides); subsequent walks with
   key 0 are accepted until the server arms the next token.
4. Independently, seq must equal the server's expectation (§2); violations get
   the same silent direction-only treatment. (Stock RunUO sends `0x21` here;
   Outlands removed the ack/deny chatter — consistent with their client fork
   not needing it and with zero genuine `0x21/0x22` on the wire.)

This explains every observation, including why "byte-identical" injections
fail: they were byte-identical to *historical* client walks, but the live
acceptance state (outstanding token, expected seq) had moved on. The token is
per-session and single-use; replayed or absent tokens are silently refused.

**Consequence for the harness:** you cannot build an accepted walk from a
static recipe — the driver must track the token stream and seq chain live
(§6). Two practical strategies:

- **Token sniping:** watch S2C for `BF sub1/sub2` (needs the decoder desync
  fixed in the movement-relevant regions — see live probe P1), and inject the
  next walk with the fresh token *before the client consumes it*. If the
  client walks first, the token is gone — wait for the next arming event.
- **Pass-through rewrite:** let the client remain the movement driver and
  rewrite/augment its `0x02` packets proxy-side (the proxy already owns the
  XOR layer).

## 5. The resync flow (Q2 answer)

C2S resync = `22 0000` (3 B; `Send_Resync @ 0x140179740` writes `0x22` and
zero-pads to `PacketsTable[0x22]` = 3) **[CODE]**.

Client triggers **[CODE]**:

1. `WalkerManager.ConfirmWalk @ 0x14030aec0` bad-step path (a S2C `0x22`
   ConfirmWalk whose seq matches no pending step) — throttled by
   `ResendPacketResync`; never observed live (no genuine S2C `0x22`).
2. `GameActions.TriggerClientResync @ 0x14020da80` — **throttled to once per
   5000 ms**, logs cliloc `0x3b2`, sets `Walker.LastStepRequestTime =
   now + 700 ms` (blocking walks during the window), sends `22 0000`. Called
   only via Razor/Assistant + plugin command wrappers (`Assistant.Client.
   ClientResync` thunk @ 0x140033030, plugin thunk @ 0x1401a2700, command
   delegates @ 0x140036e60 / 0x1400fd960) — i.e. **user/hotkey/script-driven,
   not automatic**. The resyncs seen ~45 ms after injected walks in the
   captures were operator/script actions (or a hidden ConfirmWalk), not client
   auto-detection. **[INFERENCE]** for the operator part.

Server response **[CAPTURE]** (both resyncs in session_20260928_211622):
immediate cluster containing **`0x20` (28 B)** — the V10 DrawPlayer
(`MobileUpdateV10 @ 0x140188d10`: serial, body, x/y/z u32, dir, flags;
re-anchors the player without resetting the walker) — plus the companion-tool
channel packets **`0x30` (5 B)** and sometimes **`0x95` (11 B)**:

- `0x30` = `30 00000082` both at login and in resync responses: trailing byte
  is the current facing (`0x82` = run|dir 2), leading bytes 0 in every genuine
  instance. **No handler exists** in either the CUO or the Assistant dispatch
  (checked `cuo_handler_table.json` + Assistant `AnalyzePacket` slots) — the
  client frames and discards it, exactly like the `0x00/0x40/0x52` world-data
  stream. It is the movement-state record for sanctioned companion tools, not
  a game-client control packet.
- `0x95` (11 B) = observed as `95 30 00000082 01 1d200000` — i.e. a batch
  wrapper carrying the `0x30` record plus a `0x01`-record (`01 1d200000`,
  also present standalone at login). The `0x95` slot in the CUO table points
  at the stock `DyeData @ 0x140192690` handler; whether that handler is
  vestigial is cosmetic — it carries no movement-acceptance semantics.

Stock-RunUO semantics of the resync (**[UPSTREAM]**, inherited): the server
sets `state.Sequence = 0` and clears the fastwalk state — so **the next walk
after a resync must be seq 0 and carry any newly pushed token**.
**[INFERENCE]** a token re-arm after resync is likely (stock clears the
stack); consistent with session_20260928_211622: keyed walk (key 1) 30 s after
resync-response 1.

## 6. Reference implementation

`harness/movement_driver.py` — a `MovementDriver` class that, fed the decoded
session stream (both directions), always knows how to build the next accepted
walk:

- mirrors `WalkSequence` (login/BF-sub1/deny → 0; `+1` per walk, wrap
  `0xFF→1`),
- mirrors the 5-slot token stack (BF sub1 seed+reset, sub2 push; pop on use,
  whether by the client's observed walks or ours),
- exposes `token_outstanding` / `current_token()` so the caller knows whether
  the next walk must carry a token,
- emits `02 <dir|run> <seq> <key>` plaintext (the proxy applies the session
  XOR).

Replay-validated against all four session logs: the driver reproduces every
observed client walk byte-for-byte (seq chain incl. idle persistence, single
token consumption, mid-burst token push in session 223537), given token pushes
at the timestamps the client demonstrably consumed them (the pushes themselves
are decoder-hidden — §3).

## 7. Live probes (do NOT run unattended; use the test shard)

- **P1 — surface the token packets.** Fix the S2C decoder desyncs (the
  "implausible length; dropping 1 byte" regions) enough to frame `0xBF`
  reliably, then log in and idle-walk: capture `BF 001d 0001 …` at login and
  `BF 0009 0002 …` pushes; record exact arming triggers (idle threshold?
  post-resync? post-deny?). Deliverable: definitive push timing table.
- **P2 — token validation (decisive for Q3).** After a fresh push (from P1),
  inject walk A with `key = token` and walk B with `key = token^1` (seq
  correct, 1 s apart, client still). Expect: A steps, B turns-only. Then
  inject C with `key = 0` while a second token is outstanding → expect
  turn-only; and D with `key = 0` immediately after the client consumed the
  token (correct continuing seq) → expect step. A/C vs D pins the token gate;
  B pins exact-match.
- **P3 — seq gate.** With no token outstanding, inject walks with seq =
  expected+1, then expected (1 s apart). Expect: first turns-only, second
  steps. Confirms the strict seq chain and whether the server emits any deny.
- **P4 — resync re-arm.** Inject `22 0000`, then immediately a seq-0 walk with
  key 0, then (after P1 shows the re-arm push) a seq-0 walk with the new
  token. Confirms the post-resync expectation (seq 0) and token re-arm.

## 8. Evidence index

| Claim | Where |
|---|---|
| Walk packet build, 7 B, field order | `decompiled/protocol_handlers.c:70994` (`Send_WalkRequest @ 0x140179c60`) |
| seq increment + wrap `0xFF→1` | `PlayerMobile.Walk @ 0x140328440` (headless decompile, `ghidra_movement2_out.txt`); upstream `PlayerMobile.cs:673` |
| Walk gates (WalkingFailed, cooldowns, 5 pending steps, paralyzed) | `PlayerMobile.Walk @ 0x140328440` |
| key = FastWalkStack.GetValue (pop first nonzero of 5) | `FUN_14030ada0 @ 0x14030ada0` (`ghidra_movement3_out.txt`); upstream `WalkerManager.cs:67` |
| token seed/push only via 0xBF sub1/sub2; sub1 resets walker | `ExtendedCommand @ 0x140194bc0` case 1/2 (`protocol_handlers.c:12044-12107`); xref AddValue; `OnPlayerTeleport @ 0x140190500` |
| DenyWalk handler resets walker | `DenyWalk @ 0x140189200` → `WalkerManager.DenyWalk @ 0x14030ae40` → `Reset @ 0x14030b130` |
| ConfirmWalk (S2C `22 <seq> <dir>`, 3 B) + bad-step resync | `ConfirmWalk @ 0x1401894e0`, `WalkerManager.ConfirmWalk @ 0x14030aec0` |
| Resync send `22 0000`, throttle 5 s, +700 ms walk block | `Send_Resync @ 0x140179740`, `TriggerClientResync @ 0x14020da80` |
| Server seq/token reference semantics | `ref/runuo_PacketHandlers.cs:1571,1354`; `ref/runuo_Mobile.cs:3236-3272,3075`; `ref/servuo_PacketHandlers.cs:1658` |
| V10 DrawPlayer (0x20, 28 B) doesn't reset walker | `MobileUpdateV10 @ 0x140188d10` (`protocol_handlers.c:18323`) |
| 0x30/0x95 handler-less / companion channel | `cuo_handler_table.json`; captures (login + both resync clusters) |
| Captures | `logs/session_20260928_{141253,164548,211622,223537}.jsonl` (walk streams replayed by `harness/movement_driver.py`) |

## Validated live model (2026-09-29, session 20260929_113311)

All of the following was proven with live injections on the Test Shard:

1. **Token values are constants**: login = **8**, re-arm (after each client movement-resync `22 0000`) = **1**. Not random, not per-session random. The first walk of a cycle must carry the current token; it is consumed on acceptance.
2. **Continuation walks** (after a valid token walk, before the next re-arm) work with **key 0** and a **continuing seq** — including injected walks (proven: 4-step west drive rubber-banded the character).
3. **Every accepted injected walk triggers a client movement-resync** (C2S `22 0000`) ~50–500 ms later, because the server reports movement the client didn't initiate. The resync re-arms the token requirement (new value 1) and resets the seq counter to 0.
4. **Sustained autonomous walking** = a series of single `walk(dir, seq=0, key=1)` injections at ~600–700 ms intervals (one per resync cycle). Validated: 6 consecutive injected steps moved the character ~6 tiles with no manual input at all.
5. **Resync suppression (dropping client `22 0000`) was tested and is WRONG** — it deadlocks the client's own movement recovery after agent activity. Proxy comment documents this; suppression removed.
6. Anti-cheat note: the resync-per-external-walk behavior means an agent that walks leaves a distinctive resync cadence in the logs — visible to server-side behavioral analysis. Human pacing and mixed manual play remain the mitigation.

## Acceptance gate discovered (2026-09-29, continued validation)

- The fastwalk key field is effectively ignored on continuations (user walks show seq 0,1,2 all with key 1 accepted; key 0 also accepted). Token values 8 (login) and 1 (re-arm) matter only for the FIRST walk of a cycle.
- **The binding constraint is a behavioral gate on walk trains without interleaved client activity**: short injected bursts following client activity (manual walks or a fresh resync) are accepted; long uninterrupted injected trains (10+ steps) are silently rejected wholesale (no turn, no step, no resync). This is consistent with server-side artificial-input detection (rules §4).
- Practical recipes:
  - **Attended bursts**: user activity, then ≤ ~6 injected steps — reliable.
  - **Closed-loop tasks**: interleave manual/client activity with agent bursts (the out-and-back proved this works).
  - Full-autonomy sustained walking needs the proxy-side seq/key rewrite + activity interleaving strategy (Phase 4 work, flagged as a detection-surface item for ANTICHEAT.md).
- The client movement-resync (`22 0000`) fires ~50–500 ms after any accepted external walk and is rate-limited (~5 s). It resets the expected seq to 0 and re-arms the token (value 1).

## Session state & next steps (2026-09-29, end of day)

### What is proven to work
- Full protocol stack: interception (WinDivert NAT), cipher (single-byte session XOR), framing (authoritative table), world model, and actions — speech, dclick, gumps, spells, item queries all execute via injection.
- **Movement in attended mode (validated repeatedly):** after the client opens a movement cycle with a manual walk (carrying the cycle token — 8 at login, 1 after resync/idle), injected continuation walks (key 0, correct continuing seq) execute and the client rubber-bands to the true position.
- **Proxy-side SeqAuthority** (committed `1d0ad23`): the proxy rewrites every walk's seq byte (client + injected) to one monotonic ladder; resets on login and client resync (22 0000). Proven offline (`test_seq_rewrite.py` ALL PASS: out-of-order client seqs normalized, injections continue ladder, resync resets).

### What is currently broken
- **walk_cli.py (arrow-key CLI) walks did not execute live.** Prime suspect: the CLI sends `fastwalk_key=0` by default — after login the cycle-start walk needs the login token (8), and after a resync/idle it needs the re-arm token (1). The CLI must send a token walk first (its `8`/`1` keys exist for this) or the client must open the cycle first. Untested after the authority went live.
- Client walks worked a few times then stopped — consistent with token-cycle dynamics (after external walks trigger a client resync, the cycle re-arms and the next walk needs the re-arm token again) and/or the authority's counter drifting from the server's expectation when packets are missed.
- The walk mirror tool was retired (feedback-loop class of bugs; with the authority its injected hex was indistinguishable from client walks in the log).

### Next steps (walking reliability first)
1. **Make the CLI cycle-aware**: auto-send the login token (8) on the first walk after login and the re-arm token (1) on the first walk after a client resync; key 0 otherwise. Alternatively, have the proxy track the current token from client first-walks and inject it into agent walks automatically (better: zero user burden).
2. Live-verify: CLI token walk → continuations chain (watch the shared ladder in tail_log).
3. Decide token tracking location: proxy-side (authority injects current token into key field of agent walks, mirroring how it owns seq) — then ALL tools get movement for free.
4. Then the closed-loop bank run, and Phase 4 (agent runtime).

### Tooling notes
- `walk_cli.py`: terminal arrow-key walker (arrows=walk, space=run/walk, 8/1=token walk, q=quit).
- `tail_log.py`: readable live packet tail of the active session.
- The seq authority means tools send seq 0 always; the proxy assigns the true value.

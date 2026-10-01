# Movement (walk) acceptance — Outlands (2026-09-28)

How to build a `0x02` walk packet the Outlands server will *execute* (step), not
just *acknowledge* (turn). Every claim is tagged: **[CODE]** = decompiled client
binary (Ghidra, `decompiled/protocol_handlers.c` + targeted headless decompiles),
**[UPSTREAM]** = ClassicUO-main / RunUO source, **[CAPTURE]** = session logs,
**[INFERENCE]** = reasoned from the previous three, marked where used.

Reference implementation + replay validation: `harness/movement_driver.py`
(run `python harness/movement_driver.py` — mirrors 289 client walks from all
four sessions byte-for-byte).

## ▶ Current model — from real S2C (2026-09-29). Supersedes conflicting text below.

Everything before this date inferred server behavior from C2S only, because the S2C decode was broken
(missing XOR, docs/CIPHER.md §4). With the corrected decode, the server's movement packets are
visible. **[CAPTURE]** sessions 20260929_142237 / _143051 / _144541:

| Event | Server packets (in order) |
|---|---|
| Login | four `BF 0001` seeds, tokens 5, 6, 7, **8** (last wins → "login token 8") |
| Accepted walk | `22 <seq> 01` ConfirmWalk, for agent and client walks alike |
| Honored client resync `22 0000` | `BF 0001` seed [token **1**] (resets the client walker) + `0x20` player update (re-anchors position) |
| Resync < ~5 s after the previous honored one | **nothing** (ignored: 2.64 s ignored, 5.56 s honored) |
| Rejected walk | **nothing**: no confirm, no `0x21`, and the server's expected seq does NOT reset (unlike stock RunUO) |

Consequences, all visible in the captures:
- **Token = the latest seed's value.** The next walk must carry it; continuations carry 0.
- **Seq resets only on an honored resync (seed).** After an ignored resync the server still expects
  last+1. Resetting the ladder on every C2S resync caused walk 3's rejection in 142237 and 143051.
- **Client lockout:** a confirm for a step the client didn't send → bad step → `WalkingFailed` + one
  latched resync. An honored resync brings back the seed reset. An ignored one leaves the client
  frozen. In 142237 the server confirmed 12 agent continuations (seq 1–12) while the frozen client drew
  nothing, so **no server-side walk-train gate was observed**.
- Server truth for agent steps: the player `0x20` x went `7ab → 7aa → … → 7a6` for 5 steps west
  (144541).

**Fix B (implemented, `harness/proxy.py` `MoveAuthority`; offline-proven by `test_movement.py`):**
- The seq ladder follows seeds (reset to 0) and denies. On an ignored client resync (no seed within
  1.5 s), the ladder is kept. On a **silent rejection** (no confirm within `CONFIRM_TIMEOUT_S` = 3 s),
  the ladder rewinds to the rejected walk's seq (the server doesn't advance, 142237). After 3
  rejections in a row, agent walks stall until a walk is confirmed or a seed/deny arrives.
- **Late confirms (2026-09-30).** A server hitch can confirm a walk 2.0–2.3 s after it was sent
  (20260930_123206, 091704). With the old 1.5 s timeout the confirm arrived after the proxy had
  given up: it was forwarded to the client as a confirm it never asked for (bad step → client resync
  `22 0000`, 3× in 123206), and once the agent re-sent the "rejected" seq (091704 seq 109 went out
  twice). Now expired walks stay in `MoveAuthority.late` for 5 s: a late confirm is hidden from the
  client (its walker was reset by the re-anchor), moves the tracked position and moves the ladder
  past that seq; agent walks wait until the window closes instead of re-sending the seq.
- **At most 5 walks unconfirmed**, like the stock client (`Constants.MAX_STEP_COUNT`,
  PlayerMobile.Walk): an agent walk beyond that is refused (`ERR walk gated: 5 walks unconfirmed`).
- The token is taken from seeds (and `BF 0002` pushes) and stamped into the next walk if its key is 0.
  The client's spent copy is zeroed later.
- **Agent confirms are hidden from the client** (their segment is dropped). **Client confirms are
  rewritten to the client's own seq** when the ladder differs, so agent and client walks can mix
  freely.
- **Position tracking:** anchored by the server's self `0x1B` (x@13 y@17 z@21 dir@25), `0x20`
  (x@13 y@17 dir@23 z@24), `0x77` (x@5 y@9 z@13 dir@17) and `0x21` (x@2 y@6 dir@10 z@11), then
  advanced per confirmed walk. A walk whose direction differs from the facing only turns; otherwise
  it moves one tile. Verified on 144541: the 0x1B anchor plus 6 agent walks W equals the server's
  final self `0x77` (0x7A6, 0xA25).
- **Re-anchor, client-only (user decision 2026-09-29; per step since 2026-10-01):** in place of each
  hidden agent confirm (nothing else in flight), and after 0.5 s of quiet when a rejection or late
  confirm left the client behind, the proxy writes a **fabricated S2C `0x21` DenyWalk** (`21 00 x:u32 y:u32 dir:u8
  z:i32`, 15 B) to the client only. Client code: `PacketHandlers.DenyWalk @ 0x140189200` (V10 branch
  reads u32 x, u32 y, u8 dir, i32 z) → `WalkerManager.DenyWalk @ 0x14030ae40` (clear steps, reset the
  walker, SetInWorldTile) + facing = dir & 7. **Nothing goes to the server.** A fabricated self `0x20`
  would NOT work: `MobileUpdateV10` ignores position for the player (`protocol_handlers.c:18520`
  branch). The real resync reply positions via its self `0x77` + seed. **Known limit:** confirms
  carry no z, so the re-anchor uses the last server-reported z. On stairs or slopes the client may
  show a wrong height until the next real server update. **Race:** a client key press whose walk
  crosses the fabricated deny in flight gets a confirm the client no longer has pending. That is a
  bad step, which makes the client send its own resync (client-produced) and recover. **Per step**
  (ANTICHEAT.md §10 A11): with re-anchors only after quiet, the client stood up to 53 tiles behind
  and dropped every mobile the server sent near the character (instant `bf 000c` close-status, 93 %
  vs 1-5 % for humans). The client's DenyWalk also runs its own auto-open of doors (`TryOpenDoors`
  on the position and direction change) when Auto Open Doors is on, so that setting must be off:
  `agent_link.Mover` sends the open-door request itself (user decision 2026-10-01).
- Agent gates: pacing (0.2 s run / 0.4 s walk, the Speedhack surface); waiting for the server's reply
  to a client resync; stall after 3 rejections; an expired walk's 5 s late-confirm window; 5 walks
  unconfirmed. `agent_link.Mover` and `ctl act walk` retry the self-clearing gates for up to 10 s.
- **Agent step rhythm (2026-09-30):** the Mover sends each step at the stock held-key cadence, 200 ms
  (run) / 400 ms (walk) after the previous send plus 3–15 ms jitter (`humanize.Human.step_gap`), and
  it never sends a step the map rules refuse (the client wouldn't: PlayerMobile.Walk → CanWalk). The
  check runs again right before every step with the ground items the world model has then, and a
  diagonal past a door is refused (2026-10-01, ANTICHEAT.md §10).

**Fix B VALIDATED LIVE (session_20260929_161433, user-confirmed 2026-09-29).** 57 agent walks (walk_cli)
plus 30 client walks, mixed. 55 agent confirms hidden, 5 client re-anchors via fabricated `0x21` (the
character snapped to its true position each time), **0 resyncs from client or proxy, 0 proxy-originated
C2S packets**, client arrow keys fully working throughout. **New evidence: blocked moves ARE denied.**
An agent walk into a blocked tile got a real server `21 37 000007a3 00000a0f 80 00000000` (the rejected
seq + the server position). The proxy reset the ladder to 0 and forwarded the deny, and the client
repositioned itself. So Outlands keeps stock deny semantics for movement failures; the silent
rejections seen earlier (142237/143051) are specific to wrong seq/token.

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

## Validated live model (2026-09-29, session 20260929_113311) — superseded by "▶ Current model"

Points 3, 4 and 6 describe the fix-A era: since fix B the client never sees a foreign confirm, so
no resync follows an agent walk (0 in 161433 and in the 11 sessions audited on 2026-09-30, apart
from the late-confirm case now closed).

All of the following was proven with live injections on the Test Shard:

1. **Token values are constants**: login = **8**, re-arm (after each client movement-resync `22 0000`) = **1**. Not random, not per-session random. The first walk of a cycle must carry the current token; it is consumed on acceptance.
2. **Continuation walks** (after a valid token walk, before the next re-arm) work with **key 0** and a **continuing seq** — including injected walks (proven: 4-step west drive rubber-banded the character).
3. **Every accepted injected walk triggers a client movement-resync** (C2S `22 0000`) ~50–500 ms later, because the server reports movement the client didn't initiate. The resync re-arms the token requirement (new value 1) and resets the seq counter to 0.
4. **Sustained autonomous walking** = a series of single `walk(dir, seq=0, key=1)` injections at ~600–700 ms intervals (one per resync cycle). Validated: 6 consecutive injected steps moved the character ~6 tiles with no manual input at all.
5. **Resync suppression (dropping client `22 0000`) was tested and is WRONG** — it deadlocks the client's own movement recovery after agent activity. Proxy comment documents this; suppression removed.
6. Anti-cheat note: the resync-per-external-walk behavior means an agent that walks leaves a distinctive resync cadence in the logs — visible to server-side behavioral analysis. Human pacing and mixed manual play remain the mitigation.

## Acceptance gate discovered (2026-09-29, continued validation) — refuted

The "behavioral gate" below was the client's own walker: the corrected S2C decode shows the server
confirmed all 12 agent walks of a solo train in 142237 (▶ Current model; ANTICHEAT.md §8.9). The
≤ ~6-step recipe is not implemented; the lines are kept as the record of what was believed then.

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
- **Proxy-side MoveAuthority** (supersedes the seq-only `SeqAuthority` of `1d0ad23`): the proxy owns both the seq and key fields of every walk (client + injected). Seq: one monotonic ladder, sender value always overwritten. Key:
  - Cycle opener (first walk after login / client resync `22 0000`) from the agent, or from the client with key 0 → the armed token (8 login / 1 re-arm) is stamped in, and remembered as `stale_token` — the client holds (or will receive) its own copy of that now-spent token.
  - Cycle opener from the client with its own key → passed (server-issued truth; ≠ armed token logs `c2s_token_mismatch`, which would falsify the constant-token model).
  - Continuations: agent keys forced to 0 (`c2s_agent_key_cleared`); a client key equal to `stale_token` is zeroed once (`c2s_stale_token_dropped`) so the server never sees a spent token re-presented; any other client key passes (`c2s_token_passthrough`) because genuine mid-cycle server pushes exist — session_20260928_223537 t=456.65 `02 84 04 00000001` with no resync since the previous token walk at t=417.45 (pre-proxy-rewrite capture, so client-native).
  - Login/resync clear `stale_token` (the server's seed overwrites the client's 5-slot stack, §3).
  - Residual ambiguity **[INFERENCE]**: if the server pushes a mid-cycle token with the same value as a stale one before the client presents its stale copy, the proxy zeroes the first and passes the second — the walk in between would lack the outstanding token. Can't be resolved without seeing S2C `0xBF` (decoder desync, §3).

  Tools send seq 0 / key 0 always. Proven offline (`test_seq_rewrite.py` ALL PASS, 12 checks covering every branch above). Every c2s log row carries `src` (`client`/`agent`), shown as `AGENT` in `tail_log.py`.

### Root cause of the walk_cli failure (session_20260929_134149, confirmed from the log)
- walk_cli injected 5 walks (ladder seq 0–4) with key 0 before any client walk → the login cycle opener carried no token → rejected.
- The client's own first walk (key 8) then got ladder seq 5, but the server still expected seq 0 → rejected, and every later walk (seq 6…0x22) was off-ladder → all rejected until the session ended. No resync occurred to heal it.
- Fixed by the token stamping above: the first injected walk now carries 8.

### Open risk: ladder drift after a rejected walk
Stock RunUO resets `state.Sequence = 0` on any rejected walk (§2); Outlands rejects silently (no `0x21`), so the proxy cannot see rejections and its ladder keeps counting. **[INFERENCE]** After a rejected walk (blocked tile, gate), the ladder is ahead of the server until the next client resync resets both. Watch for this in live runs: a chain that stops stepping mid-burst with no resync in the log is the signature. Fix direction if it bites: detect acceptance from S2C (needs the decoder desync fixed) or force a resync-equivalent re-anchor.

### Live result + client lockout mechanism (session_20260929_142237)
Live run of walk_cli with MoveAuthority: 3 agent cycle openers (`seq 0 key 8`, `seq 0 key 1` ×2, ~1.1 s apart; first two each followed ~50 ms later by a client `22 0000`), then 12 agent continuations (seq 1–0x0C, 0.25–0.5 s apart) with **no further resync**. User saw one turn and no steps; afterwards **the client's own arrow keys emitted no walk packets at all** (zero client `0x02` in the whole session).

Mechanism **[CODE upstream `ClassicUO-main/.../WalkerManager.cs:125-198`, `PlayerMobile.cs:525`; same functions in the Outlands fork per §8 table — `WalkerManager.ConfirmWalk @ 0x14030aec0`, `Reset @ 0x14030b130`]**:
1. The server confirms accepted walks with S2C `22 <seq> <noto>`, and it cannot tell agent walks from client walks. **[INFERENCE — strong]** The client must receive confirms (with none it stalls at `MAX_STEP_COUNT` = 5 pending steps, yet 165-walk client runs exist), even though the lossy S2C decoder rarely surfaces them (this session: one `22 01 00` hit at t=11.58, could be a misframe).
2. A confirm whose seq matches none of the client's pending steps is a **bad step**: `WalkingFailed = true`, pending steps cleared, and **one** `Send_Resync` guarded by the `ResendPacketResync` latch.
3. `PlayerMobile.Walk` returns false while `WalkingFailed` is set, so the client stops sending walks entirely. Only `WalkerManager.Reset` (S2C DenyWalk, `BF sub1` seed → `OnPlayerTeleport`, legacy `0x20` UpdatePlayer) clears `WalkingFailed` and the latch. The server's response to a resync evidently includes the reset (the client restarts at seq 0 with token 1 afterwards).
4. So: **the ~50 ms client resync after each external walk is this bad-step path**, not operator action (corrects §5's attribution). The single-step-per-resync recipe worked because each resync response reset the client. A train of agent walks produces more foreign confirms than the reset cycle can clear. Once the latch is set with no reset pending, the client is stuck: no walks, no resync. That is the "manual-input lockout" previously attributed to a server penalty (ANTICHEAT §8.9, now reinterpreted).
5. Why the resyncs stopped after the 3rd opener is unresolved. Either that walk was rejected silently and the ladder drifted (see Open risk), or the latch was set while a reset was pending. Resolving it needs S2C visibility.

Recovery: relog (fresh `WalkerManager`), or anything that makes the server reset the walker (e.g. a client-initiated resync from the stock client's own UI, if available).

Fix options: (A) proxy gates agent walks to one per resync cycle, after the reset has landed (no new capability; ~1 step per 0.7 s; keeps the resync-per-step signature). (B) Hide agent confirms from the client (S2C rewrite) and re-anchor it deliberately. Clean, but blocked on S2C framing. **Huffman flush markers do not align with packets** (session 113311: 7289 flush segments; 0x22-led segments of 5/10/14 B), so filtering at the compressed-segment level is impossible. B needs plaintext framing fixed (probe P1) plus a Huffman re-encoder.

**Decision (user, 2026-09-29): A now, then B.** A is implemented in `MoveAuthority.agent_walk_block`: an agent walk is refused (`ERR walk gated: …`, not relayed) unless a movement cycle is armed (no walk since login/resync), and after a resync only once `AGENT_SETTLE_S` = 0.65 s have passed. So agent walks are always cycle openers (seq 0 + stamped token), never continuations. Client walks are never gated. Once the agent opens a cycle, the next agent step waits for the client's bad-step resync. If that resync never comes (the walk was rejected silently), the agent stays gated until the client walks or resyncs, which is the safe failure. `inject.py walk` retries gated sends for up to 5 s per step. Offline proof: `test_seq_rewrite.py` (14 checks) and `harness/test_actions.py` ("second agent walk in cycle gated").

**Live test of A (session_20260929_143051) and spacing correction.** Agent walk 1 (token 8) stepped. Walk 2 (token 1, 2.6 s after resync 1) got a client resync, and walk 3 (token 1, 4.1 s after resync 2) got none. The user saw only the first walk work. In all three agent-walk sessions the second client resync (0.76 / 1.14 / 2.64 s after the first; sessions 113311 / 142237 / 143051) was followed by silence: the next agent walk drew no resync. **[INFERENCE]** The server ignores a resync arriving within ~5 s of the previous one, like the client's own 5000 ms `TriggerClientResync` throttle. No walker reset comes back, the client stays latched (`WalkingFailed` + `ResendPacketResync`), and the gate waits forever for a resync. Walks sent in that state may still execute server-side (113311 reported ~6 tiles). The client just doesn't draw movement it didn't request until a re-anchor, so "no visible step" is not proof of rejection. Correction: the settle window `AGENT_SETTLE_S` = 0.65 s is replaced by `RESYNC_SPACING_S` = 5.0 s between the last client resync and an agent walk, i.e. ~1 agent step per 5 s. The 5 s value is inferred from one data point above the threshold (2.64 s ignored) plus the client constant.

**Fix A VALIDATED LIVE (session_20260929_144541).** 6 agent walks via walk_cli, each a cycle opener (token 8, then token 1 ×5). Every one was followed ~50 ms later by a client resync, spaced 6.72 / 7.92 / 6.24 / 5.56 / 13.75 s apart. The user saw every walk step. Afterwards the client's own arrow keys worked: 27 client walks at the normal 0.1–0.2 s cadence (seq 0 → 0x1A). The first opened the cycle with the client's own token 1, passed with no `c2s_token_mismatch`, which confirms the re-arm value 1 live. This supports the resync-spacing explanation: 2.64 s ignored, ≥ 5.56 s honored, so the server threshold lies somewhere in (2.64, 5.56] s. The client-side lockout explanation also holds up: with spacing respected, no lockout. Cost: ~1 agent step per 5 s plus the resync-per-step signature.

### Next steps
Fix B is live-validated (see "▶ Current model" at the top).
1. Closed-loop bank run (Phase 3 done-criterion), then Phase 4 (agent runtime).
2. Optional: map-based z for the re-anchor (read client map/statics/tiledata read-only) if height
   drift shows up on stairs/slopes.

### Tooling notes
- `walk_cli.py`: terminal arrow-key walker (arrows=walk, space=run/walk, q=quit).
- `tail_log.py`: readable live packet tail of the active session; `AGENT` / `PROXY` mark non-client
  packets. Movement events: `s2c_fastwalk_seed`, `s2c_confirm_hidden`, `s2c_confirm_rewritten`,
  `reanchor_client`, `resync_ignored`, `walk_rejected`. The fabricated deny is logged as an s2c row
  with `src: proxy`.

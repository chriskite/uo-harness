# VISUALIZER.md — human view of the harness's state

Status: **Phase A BUILT 2026-09-29** (M1–M5; M6 waits on Phase 4). See §9 for run instructions and
how the build deviates from this design. The design below (revised 2026-09-29) supersedes the
2026-09-28 draft, which predated the S2C decode fix, the proxy's live world model + state port,
MoveAuthority, walk memory and the bank errand. What changed and why is in §8.

Goal: a web view, read-only except for the agent gate (§2.2), showing a human exactly what the harness knows, both the **world**
(self, mobiles, items, gumps, events) and the **harness itself** (server-true movement, agent vs
client traffic, hidden/rewritten confirms, client re-anchors, walk memory). It works against the
**live proxy** or a **captured session replay**, with identical data in both.

Grounded in: `harness/proxy.py` (`SessionTap.state()`, `handle_state`), `harness/world/{state,
runtime}.py`, `harness/replay.py`, `harness/nav.py`, `harness/errand_bank.py`.

---

## 1. Data: the state-port contract

The visualizer consumes the **same state-port response the agent consumes** (the errand runner
today, the Phase 4 agent later). There is no privileged channel (§3).

`{"op": "state", "since": N}` on 127.0.0.1:25942 (JSON lines) returns:

```json
{
  "ok": true,
  "movement": {"pos": [1963, 2597, 0, 2], "self_serial": 607093, "inflight": 0,
               "next_seq": 12, "resync_pending": false, "rejects_in_row": 0,
               "stalled": false, "client_stale": false},
  "world": { ...StateStore.snapshot()... },
  "events": [ ...world-model events with absolute index >= N... ],
  "next": 1204,
  "world_errors": 0
}
```

### 1.1 `movement` = server truth
Built by the proxy's `MoveAuthority` from the server's own packets: self anchors from
0x1B/0x20/0x77/0x21, plus each ConfirmWalk (a walk in a new direction only turns). **This is the
player position to draw.** `world.self.x/y` is the world model's own estimate. Since 2026-09-29
it moves only on server confirms with the same turn rule as the proxy, and in exact replay of 163420
it equals `movement.pos` at all 23,652 settled points (0 mismatches). It can still differ while
walks are in flight, or in approximate-order replays (all C2S before all S2C). The UI shows both
and badges any disagreement.

### 1.2 `world` = `StateStore.snapshot()`
Serial keys are `0x%08X` strings. `None` fields are omitted. Current facts:
- `self`: serial, name, x/y/z/direction (absolute from 0x1B, advanced by confirmed walks), vitals, stats,
  skills, gold, weight, warmode, notoriety.
- `mobiles`: now carry **x/y/z/direction** from 0x20/0x77/0x78 (163420: 14 of 15 have a
  position), plus name, graphic, hue, vitals, notoriety, flags.
- `items`: ground items carry x/y/z; contained/equipped items carry `container` (hex parent),
  `layer`, `grid`. The bank box is the self item on layer 0x1D (graphic 0x0E7C).
- `gumps`, `target`, `census` (serials the client queried), `names`, `buffs`, `containers` (open
  set), `characters`.
- Size: about 30 KB for a town scene (163420: 15 mobiles, 180 items). Full-snapshot pushes at
  ≤4 Hz are fine on localhost.

### 1.3 `events` = world-model events
Flat `{"ev": ..., ...}` dicts. **Serials are ints** (the snapshot uses hex strings; the frontend
normalizes). Vocabulary (`runtime.py` `_emit`):
- Session: `login`, `char_select`, `character_list`, `dialect_handshake` (flag1/flag2 = the
  S2C/C2S XOR keys), `keepalive`
- Movement: `walk`, `walk_confirm`, `walk_deny`
- Entities: `query`, `item_query`, `names`, `item_seen`, `delete`, `animation`
- Text: `speech` (C2S; now includes `keywords` for encoded speech), `speech_heard` (S2C
  0x1C/0xAE; type 6 = click labels such as "Len the banker")
- Interaction: `dclick`, `container_open`, `container_content`, `gump_open`, `gump_response`,
  `target`, `target_response`
- Combat and buffs: `damage`, `swing`, `spell_cast`, `buff_update`, `buff_remove`

Session 163420 produced 1204 events, 719 of them `keepalive`, hidden by default in the UI.
Silent mutations (warmode, stats, skills, vitals) arrive only via snapshots, so the UI uses both
channels.

### 1.4 Required proxy additions (small; part of M1)
1. **Harness events in the event stream.** The proxy currently writes its movement decisions only
   to the jsonl log. Also append them to the state-port event log, tagged `"origin": "proxy"`:
   `step`, `blocked`, `walk_rejected`, `resync_ignored`, `reanchor_client`,
   `s2c_confirm_hidden`, `s2c_confirm_rewritten`, `c2s_token_*`, `c2s_resync_seen`, and one
   `c2s` summary event per agent-originated packet (`{src, id}`). This makes agent activity and
   proxy interventions observable, and it is data the agent should see too (parity).
2. **Event envelope.** Store events as `{"seq", "t", "data"}` at append time (`t` = wall clock
   when the proxy saw the packet). Today events have no timestamps, and drain-time stamping would
   be wrong under polling.
3. **Diagnostics.** Add `"diagnostics": {packet_counts top-N, unhandled, parse_failures,
   anomalies, world_errors}` to the response. It is labeled meta-information, the one non-world
   field (§3).
4. **Cheap polling.** Add an optional `"snapshot": false` request flag, so a poller can fetch only
   movement + new events between full snapshots.

---

## 2. Architecture

```
 LIVE:    client ⇄ proxy.py (relay + SessionTap + WorldRuntime + MoveAuthority) ⇄ server
                        │ state port 127.0.0.1:25942 (JSON lines)
                        ├──► errand_bank.py / Phase 4 agent
                        └──► viz_server.py ──HTTP :8080 (REST + SSE)──► browser (viz/ React)

 REPLAY:  logs/session_*.{c2s.raw,s2c.raw,jsonl}
              └──► viz_server.py: offline SessionTap driver (same class as the proxy)
                        └──HTTP :8080 (same API)──► browser
```

- **`viz_server.py` is a separate process that observes, plus one switch.** In live mode it polls
  the state port (~4 Hz, `since` cursor) and fans out to the browser via SSE. It never touches the
  control port, never injects anything and never sends anything toward the game server
  (ANTICHEAT §8). Its only write is the proxy's agent gate (§2.2), which decides whether agent
  injections are refused; that is the one thing a human can do from the viz. A viz crash cannot
  affect the game connection. The 2026-09-28 draft embedded the relay in the viz server; that is
  rejected now that the proxy already runs the world model.
- **Replay runs the proxy's own `SessionTap` offline**, not a separate decoder. The driver feeds
  the capture's packets into a `SessionTap` (dummy writers) in their original interleave, so
  replay `tap.state()` is exactly what live would have served. That includes MoveAuthority truth,
  which the world-model-only replay (`replay.py`) cannot reproduce.
  - **Interleave recovery.** For captures made by the fixed proxy (session 20260929_161433
    onward; decode fix = commit 5eb2138), jsonl rows map 1:1, in order, onto the raw packets per
    direction: c2s rows onto framed raw C2S (post-rewrite, as sent), s2c rows onto
    `uo.s2c.S2CStream` packets. Verified on 163420: 3655/3655 s2c and 879/879 c2s rows match
    their packets' hex. Each jsonl row carries `t` and `src`. Merge by `t` and replay with real
    cadence and the correct `src`, which the MoveAuthority needs. The driver verifies `id`/`len`
    agreement row by row and fails loudly on a mismatch.
  - **Older captures** have garbage s2c jsonl rows (pre-fix decode) and, for three sessions,
    empty c2s.raw. They replay in canonical order (all C2S then all S2C, as `replay.py` does),
    badged "approximate order". Their movement truth is unreliable, and the UI says so.
- **Backend stack: stdlib only.** `http.server.ThreadingHTTPServer`, a poller/feeder thread, and a
  `queue.Queue` SSE fanout. HTTP threads serve a pre-serialized snapshot string that the
  poller/feeder thread swaps atomically (≤4 Hz, coalesced).

### 2.1 HTTP API (viz_server → browser)

| Route | Content |
|---|---|
| `GET /api/state` | the latest state-port response, verbatim (movement + world + recent events + diagnostics) |
| `GET /api/events` | SSE: `event: state` (full response, dirty-checked, ≤4 Hz) and `event: world_event` (each event envelope verbatim); `id:` = event seq; `Last-Event-ID` resume from a 2000-entry ring |
| `GET /api/walkmem` | `harness/data/walkmem.json` (tiles, edges, blocked), re-read when the file changes |
| `GET /api/health` | viz_server mode (live/replay + session tag + order quality), poll lag, connection status, proxy diagnostics |
| `POST /api/playback` | replay only: `{play,pause,rate,step}` |
| `GET /api/gate` | live only: the proxy's agent gate (`{"op":"gate"}` on the state port), verbatim |
| `POST /api/gate` | live only: `{"action": "pause"\|"resume"\|"kill"}`, forwarded as `{"op":"gate","action":...}` (§2.2) |
| `GET /`, `/assets/*` | built frontend (`viz/dist/`) |

SSE rather than WebSocket: the flow is strictly server→browser, the browser `EventSource` gives
reconnect and resume for free, and it adds no dependencies. Full snapshots, not diffs: the store
is last-write-wins and small.

### 2.2 Agent gate controls

The proxy's agent gate (`harness/agent_gate.py`): a manual pause, a kill switch, forced jittered
breaks after ~2 h of agent-active time, and an 8 h/day agent-active cap. While it is closed every
agent injection on the control port gets `ERR <reason>`; client traffic and the relay are
untouched. Every state-port response (including `{"ok": false, "error": "no active session"}`)
carries a top-level `gate` object (`state` running/paused/break/budget_exhausted/killed with
precedence killed > paused > break > budget_exhausted, `blocked`, `reason`, `paused`, `killed`,
`break_until`, `next_break_in_s`, `active_today_s`, `daily_cap_s`, `daily_remaining_s`, `day`,
`now`), so the viz reads it from the state frames it already streams.

**Route.** `POST /api/gate` with `{"action": "pause"|"resume"|"kill"}` opens a short-lived
state-port connection of its own (the poller's connection and `since` cursor are untouched), sends
`{"op":"gate","action":...}`, and returns the proxy's JSON: **200** when `ok`, **409** otherwise
(e.g. `resume` while killed; the reply still carries `gate`). It then wakes the poller so the next
state frame carries the new gate. Any other action, **`rearm` included, is 400 and never
forwarded**: rearming after a kill is CLI-only by policy (`python harness/agent_gate.py rearm`).
Replay has no gate: **409** `no gate in replay`. Proxy unreachable: **502**. `GET /api/gate`
forwards `{"op":"gate"}` the same way.

**UI** (header, live only; nothing in replay; `gate ?` when the state carries no gate, i.e.
proxy down or a pre-gate proxy):
- state badge (running green, paused amber, break blue, daily cap amber, KILLED red) and the
  `reason` text agents get;
- Pause/Resume toggle (Resume while manually paused; it clears only the manual pause, not a break
  or the cap). Pause and Kill stay available during a break or at the cap;
- Kill with a confirm step (`■ kill` → `confirm kill` / `cancel`, auto-cancels after 5 s). Once
  killed both buttons are disabled and the header shows
  `rearm is CLI-only: python harness/agent_gate.py rearm`;
- `next break in H:MM:SS active`: agent-active time left, so it only runs down while the agent
  works; during a break, `break until HH:MM:SS (M:SS left)` instead;
- `today 3:12 / 8:00`: agent-active time today against the cap.

After a press, the reply's gate is shown at once (`VizStore.setGate`); a state frame polled before
the press (older gate `now`) does not undo it.

---

## 3. Parity principle

The viz consumes exactly the state-port contract, the agent's contract. If the human can't see
something, neither can the agent, and vice versa. Server-side enrichment is limited to transport
(`seq`, `t`, which are part of the contract after §1.4) and replay bookkeeping. `diagnostics` is
labeled meta-information. The int-vs-hex serial wart is normalized only in the frontend
(`serial.ts`), not hidden server-side.

Uses: audit agent decisions against exactly what was observable (replay + scrub to the event);
spot coverage gaps ("the gump opened in game but nothing changed here"); reproducible eval
fixtures ("replay X, state at event N").

---

## 4. Frontend components (`viz/`, React + TSX, built with Bun)

```
┌────────────────────────────────────────────────────────────────────────────┐
│ header: LIVE :25942 | REPLAY <tag> (exact|approx order) • conn • ⏯ ⏩ step   │
│         gate: state+reason • ⏸/▶ • ■ kill • next break / break end • today  │
├────────────────┬───────────────────────────────────────┬───────────────────┤
│ SelfPanel      │                                       │ EventLog          │
│────────────────│              MapGrid                  │ (world + proxy    │
│ MovementPanel  │   (walk memory underlay, entities,    │  origin, filter)  │
│────────────────│    truth vs dead-reckoned marker)     │                   │
│ TrafficPanel   │                                       │                   │
├────────────────┴───────────────────┬───────────────────┴───────────────────┤
│ ContainerTree                      │ EntityInspector / GumpViewer / Census │
└────────────────────────────────────┴───────────────────────────────────────┘
```

- **SelfPanel.** `world.self`: name, serial, vitals bars, stats, gold/weight, warmode,
  notoriety, buffs (`world.buffs[self]`), skills (top N; values ×10 fixed point, joined with
  `skill_names`).
- **MovementPanel (new, harness state).** From `movement`: true position and facing; the
  world-model position with a **DIVERGED** badge when they differ; inflight walks; ladder
  `next_seq`; `rejects_in_row` / **STALLED**; resync pending; client stale (re-anchor due). Below
  that, the last N proxy movement events (step, blocked, rejected, reanchor, token stamp/drop).
- **TrafficPanel (new).** From proxy-origin events: C2S packets by source (client vs agent) per
  packet id, confirms hidden and rewritten, fabricated client packets, and the last agent action.
  The one-glance answer to "what is the agent doing, and what did the proxy change".
- **MapGrid.** One `<canvas>`, centered on the true position, wheel zoom, drag pan.
  - **Underlay: walk memory** (`/api/walkmem`, plus live `step`/`blocked` events): known-walkable
    tiles shaded, confirmed edges faint, blocked moves as red ticks. It is the best geography
    available until map files are decoded (Phase B), and it shows the planner's world directly.
  - Overlay: ground items (dots), **mobiles at their positions**, labeled with name plus title
    from type-6 `speech_heard` labels (e.g. "Len the banker"), notoriety colored; self at the true
    position with a facing arrow, and a hollow ghost marker at the world-model position when
    diverged.
  - Trail: the last N true positions from `step` events.
  - Later: the agent's planned route, once the agent publishes its intent (§6 M6).
- **EntityInspector.** Lookup `mobiles` → `items` → `names`-only, plus census and buffs;
  container jump-links.
- **ContainerTree.** Roots: ground and self. Children via `container`; equipment grouped by
  `layer` (0x15 backpack, 0x1D bank box); OPEN marks from `containers`.
- **GumpViewer.** Raw layout string plus numbered text lines, exactly what the agent sees.
  Schematic rendering is deferred.
- **CensusPanel.** `world.census` sorted by queries: what the client chose to look at. It now
  also shows the client's automatic name requests for mobiles in range (NOTES.md).
- **EventLog.** Merged world + proxy events. The filter vocabulary comes from observed `ev`
  names. Keepalive is hidden by default. Clicking a serial selects it; serials render in hex.
  Display ring 500.

---

## 5. Fidelity roadmap

- **Phase A (this plan):** schematic canvas plus the walk-memory underlay. No art, no map files.
- **Phase B, real terrain underlay:** decode the install-dir map files (read-only). The install dir
  has `map0..3.uoo`, `facet00..05.mul`, `landtiles.uoo`, `landdata.uoo`, `art.uoo`,
  `artdata.uoo`. The format is uncharacterized [INFERENCE: `.uoo` is the fork's container
  format]. Steps: hexdump/entropy scan against the known MUL layouts, then Ghidra the fork's
  `UOO.TerrainFile` / `Chunk::Load` (NOTES.md §Ghidra), then `harness/uo/mapfile.py` + tests.
  A side benefit that is **worth more than the visuals**: real terrain gives the re-anchor
  its true **z** (the known limit in MOVEMENT.md) and gives nav.py real collision data instead of
  learned walk memory. Effort 1–3 days, all of it in the format unknown.
- **Phase C, sprites and hues:** art/tiledata/hues decode for single-frame sprites, reusing
  Phase B's file access. 2–4 days. Demo value only.

---

## 6. Build plan

### Toolchain
- Backend: Python 3.13 stdlib only.
- Frontend: **Bun 1.4.2** (installed 2026-09-29 at `C:\Users\chris\.bun\bin\bun.exe`, user
  PATH) with **React + TSX**. Bun is the package manager (`bun install`), bundler (`bun build`)
  and test runner (`bun test`); type checking is `bunx tsc --noEmit`. No Vite and no Node:
  Bun bundles TSX natively.
- Dependencies: `react`, `react-dom`; dev: `@types/react`, `@types/react-dom`, `typescript`.
  No state library (`useSyncExternalStore`), no router, no CSS framework, no canvas library.
  `viz/node_modules/` and `viz/dist/` are gitignored; `bun.lock` is committed.

### Files

| File | Contents |
|---|---|
| `harness/proxy.py` | §1.4 additions: proxy events + envelope in the event log, `diagnostics`, `snapshot:false` |
| `harness/viz_feed.py` | `StatePortPoller` (live) and `ReplayDriver` (offline SessionTap; jsonl interleave recovery; exact/approx badge; playback control) |
| `harness/viz_server.py` | `--live [--state-port 25942]` / `--replay TAG`; routes §2.1; SSE ring + resume; walkmem file watch; agent gate forwarding (§2.2); serves `viz/dist` |
| `harness/test_viz.py` | backend tests (§7) |
| `viz/package.json`, `bun.lock`, `tsconfig.json`, `index.html` | scaffold; scripts `build` (`bun build src/main.tsx --outdir dist`), `watch`, `test`, `typecheck` |
| `viz/src/types.ts` | TS types for §1 (StateResponse, Movement, Snapshot, Mobile, Item, Gump, EventEnvelope, WalkMemory) |
| `viz/src/api.ts`, `store.ts`, `serial.ts` | SSE client + resume; `useSyncExternalStore` store; int↔hex serial normalization and entity lookup |
| `viz/src/gate.ts`, `components/GateControls.tsx` | agent gate badge vocabulary and button rules; the header gate controls (§2.2) |
| `viz/src/App.tsx`, `components/*.tsx`, `App.css` | §4 panels |

### Milestones

- **M1: proxy additions + replay backend + REST.** §1.4 in `proxy.py`; `viz_server.py --replay`;
  `/api/state`, `/api/walkmem`, `/api/health`.
  *Acceptance:* `--replay 20260929_163420` reproduces the errand. Final `movement.pos ==
  [1963, 2597, …]`, the bank-box `container_open` is present, the proxy events include 36 `step`
  and 2 `reanchor_client`, and the order badge reads "exact". `/api/state` equals the offline
  `SessionTap.state()` (test).
- **M2: SSE + frontend shell (Bun).** EventLog, MovementPanel, TrafficPanel.
  *Acceptance:* paced replay of 163420 streams gapless seqs; a browser reconnect resumes via
  `Last-Event-ID` without duplicates; the TrafficPanel shows 54 agent walks, 1 agent speech and
  54 hidden confirms.
- **M3: remaining Phase A UI.** MapGrid (walk memory, mobiles, labels, trail), SelfPanel,
  ContainerTree, EntityInspector, GumpViewer, CensusPanel.
  *Acceptance:* the 163420 replay shows Len labeled "Len the banker" at his position, the true
  marker walking the errand route over the walk-memory underlay, and the bank box (layer 0x1D)
  OPEN in ContainerTree.
- **M4: live mode.** `viz_server.py --live` reads the running proxy's state port.
  *Acceptance:* during a live `errand_bank.py` run the true marker tracks the character and
  proxy events appear within ~1 s. The proxy's jsonl shows **no packets caused by the viz**
  (read-only proof). Nothing on the viz side depends on stopping or restarting the proxy.
- **M5: replay scrubber.** Play, pause, rate and single-step.
  *Acceptance:* single-stepping a `step` event moves the true marker one tile; 8× plays 163420
  to completion with identical final state.
- **M6 (later, Phase 4 tie-in): agent intent channel.** The agent publishes its current
  goal/plan/route through a small proxy-side "intent" slot included in the state response, and
  the MapGrid draws the route. Scoped with the Phase 4 runtime, not here.

---

## 7. Testing

- **Backend (`harness/test_viz.py`, plain `check()` style, private ports):**
  - Replay parity: `/api/state` on 163420 equals `SessionTap.state()` from the offline driver.
  - Interleave recovery: exact order on post-fix sessions (row-by-row id/len match); approximate
    fallback on 141253.
  - SSE framing: `id`/`event`/`data`, monotonic seq, envelopes verbatim.
  - Resume: `Last-Event-ID` replays the missed suffix plus a fresh state, with no duplicates.
  - Coalescing: a burst yields one `state` message.
  - Live poller against a proxy subprocess fed by the `test_errand.py`-style fake server; also
    asserts **zero C2S packets with `src` other than client/agent**, so the viz never injects.
  - Agent gate: `/api/gate` 409 in replay, 400 for `rearm`, 502 with the proxy down; live pause
    (the proxy's own `{"op":"gate"}` reports paused, and a control-port injection from the test
    gets `ERR agent paused`), resume, kill, and resume-while-killed 409. The proxy's control-port
    log shows only the test's own connection.
- **Proxy:** extend `test_movement.py` to check that proxy events and envelopes appear in the
  state-port event log.
- **Frontend (`bun test`), pure helpers only:** serial normalization, entity lookup precedence,
  EventLog filters, ContainerTree builder, walk-memory layer builder, true-vs-dead-reckoned
  divergence rule, duration formatting, gate button rules per state, and the store keeping a
  newer gate over an older state frame.
  `bunx tsc --noEmit` must pass. No DOM snapshot tests; M2–M4 acceptance runs
  are the integration check.

---

## 9. As built (2026-09-29)

**Run**
- Build the frontend once: `cd viz && bun install && bun run build`. Bun lives at
  `C:\Users\chris\.bun\bin\bun.exe`; `bun test` runs the tests and `bun run typecheck` runs
  `tsc --noEmit`.
- Live: `python harness/viz_server.py --live [--state-port 25942] [--port 8080]`, then open
  http://127.0.0.1:8080/. It only talks to the proxy's state port (polling, plus the agent-gate
  ops of §2.2), never opens the control port, and can be started or stopped at any time. A proxy started before this build shows world events only;
  restart the proxy for proxy events, traffic and diagnostics.
- Replay: `python harness/viz_server.py --replay <TAG> [--rate 8] [--paused]` (TAG =
  `logs/session_<TAG>.*`).

**Files:** `harness/viz_feed.py` (Feed base: 2000-envelope ring, SSE fanout, ≤4 Hz pump;
`StatePortPoller`; `ReplayDriver`), `harness/viz_server.py`, `harness/test_viz.py`, and `viz/`
(React 19 + TSX; `build.ts` bundles into `viz/dist/assets/main.{js,css}`; dev dependencies also
include `@types/bun` so `bun:test`/`Bun.build` type-check).

**Deviations from and additions to the design**
- **Late-joiner state** (found during the build): click labels and traffic counts existed only as
  events, so a viewer or agent connecting after they scrolled out of the ring lost them. Fixed in
  the state itself: `world.labels` (the latest type-6 click label per serial, e.g.
  `"0x000001EA": "Len the banker"`) and a top-level `traffic` block
  (`{proxy_events: {ev: n}, c2s: [[src, id, n]]}`, cumulative for the session). The errand runner
  also reads banker labels from `world.labels` now.
- **Replay timers.** A fixed simulated tick grid produced a spurious third re-anchor on 163420 (a
  0.531 s walk gap against REANCHOR_IDLE_S = 0.5, i.e. phase-dependent). So the ReplayDriver
  replays timer decisions (walk_rejected, resync_ignored, reanchor_client) at the recorded jsonl
  `t` and ticks before each agent packet. Anything not reproduced is counted in
  `health.timer_divergences` (0 on 163420). SessionTap takes injectable `wall`/`mono` clocks.
- **Replay limit:** raw C2S is written after rewriting, so a client walk's original seq/key is
  lost. Client-side `c2s_token_*` and `s2c_confirm_rewritten` events cannot be reproduced;
  agent-side ones are.
- **SSE:** `?since=N` works as well as `Last-Event-ID`. A proxy session restart resets seqs to 0;
  the frontend detects `next` going backwards and refetches.
- **Verified in a real browser** (headless Chromium, 1600×900) on the 163420 replay:
  - the amber trail follows the errand to "Len the banker" and back
  - the true marker ends at (1963, 2597) E, while the world-model ghost sat at (1964, 2594) with
    a DIVERGED badge: the old request-based dead reckoning counted turns as steps (fixed, below)
  - Traffic shows 54 agent walks, 1 agent speech, 54 confirms hidden
  - the bank box 0x44D78CA8 is OPEN
  - step advances playback; state frames arrive in 27–95 ms
  - no page errors

**Resolved 2026-09-29:** the world model used to move self on every walk *request*, counting
turns and rejected walks as steps, so it diverged during agent walking. It now moves only on
server confirms, with the turn rule (runtime.py `_h_walk` / `_h_confirm_walk`). On the 163420
exact replay it matches `movement.pos` at every settled point. `movement.pos` remains the
authoritative field for agents (it is also correct while confirms are in flight).

## 8. Changes from the 2026-09-28 draft

| Draft said | Now | Why |
|---|---|---|
| Live viz embeds the proxy relay (`on_packet` hook in `_drain_*`) | separate read-only process on the state port | the proxy already runs WorldRuntime + state port (commit 0113ed3); those hook points no longer exist |
| Inputs = `snapshot()` + `drain_events()` | state-port response: movement truth + snapshot + indexed events | the agent's real contract; parity |
| Player position = world `self` (dead-reckoned) | `movement.pos` (server truth); self shown as a ghost when diverged | dead reckoning misses hidden/rejected walks and replay ordering |
| Mobiles have no coordinates (M6 prerequisite) | mobiles positioned via 0x20/0x77/0x78 | done in the S2C world-model migration |
| 19-byte prelude, custom S2C dialect, 1-byte resync replay | 13-byte prelude, XOR(byte 11) + per-packet Huffman (`uo.s2c`) | CIPHER.md §4 correction |
| No harness-state view | MovementPanel, TrafficPanel, proxy events, walk-memory underlay | the user asked for the harness's state; MoveAuthority/nav didn't exist |
| Replay = `replay.py` world model, jsonl `(dir,id,len)` timing match | offline SessionTap driver with exact jsonl interleave (post-fix captures) | reproduces movement truth; post-fix jsonl rows are 1:1 with packets |
| React + Vite + TS via Node | React + TSX via Bun | user decision 2026-09-29; Node absent; Bun installed |
| Map underlay Phase B only | walk-memory underlay in Phase A; Phase B also yields true z + collision | nav.py exists; z is MOVEMENT.md's known limit |

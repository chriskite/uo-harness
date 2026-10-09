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
  position), plus name, graphic, hue, vitals, notoriety, flags, `seen_t`. Only what the client
  still has (pruned like the client since 2026-10-01, docs/WORLDMODEL.md §7).
- `last_seen` (mobiles the client dropped: fields as then, plus `t`, `facet`, `why` range | facet |
  dead | delete), `swings` (latest 0x2F per attacker), `view_range` (0xC8).
- `items`: ground items carry x/y/z; contained/equipped items carry `container` (hex parent),
  `layer`, `grid`. The bank box is the self item on layer 0x1D (graphic 0x0E7C).
- `gumps`, `target`, `census` (serials the client queried), `names`, `buffs`, `containers` (open
  set), `characters`.
- Size: about 30 KB for a town scene (163420: 15 mobiles, 180 items); ~170 KB in the Shelter
  Island woods (2026-10-05: 475 items, 206 last_seen). It used to grow all session, because closed
  gumps were never dropped: 1.84 MB at 16:25 on 2026-10-05, 1.69 MB of it 1134 gumps. Since
  2026-10-05 the world keeps open gumps plus the 20 newest closed ones (docs/WORLDMODEL.md §7).
  Full-snapshot pushes at ≤4 Hz are fine; a reader that can't keep up skips states (§2.10).

### 1.3 `events` = world-model events
Flat `{"ev": ..., ...}` dicts. **Serials are ints** (the snapshot uses hex strings; the frontend
normalizes). Vocabulary (`runtime.py` `_emit`):
- Session: `login`, `char_select`, `login_confirm` {serial: the player serial, S2C 0x1B},
  `character_list`, `dialect_handshake` (flag1/flag2 = the S2C/C2S XOR keys), `keepalive`
- Movement: `walk`, `walk_confirm`, `walk_deny`
- Entities: `query`, `item_query`, `names`, `item_seen`, `delete`, `prune` {serial, why: range |
  facet | dead} (a mobile the client dropped without a 0x1D), `animation`
- Text: `speech` (C2S; now includes `keywords` for encoded speech), `speech_heard` (S2C
  0x1C/0xAE; type 6 = click labels such as "Len the banker"), `cliloc` (S2C 0xC1/0xCC:
  `cliloc` number + tab-separated `args`; render with `harness/uo/cliloc.py`)
- Interaction: `dclick`, `container_open`, `container_content`, `gump_open`, `gump_response`,
  `target`, `target_response`, `lift`, `drop`, `equip_request`, `command` (C2S 0x12),
  `popup` / `popup_request` / `popup_select` (context menus), `buy_list` / `buy` (vendors)
- Combat and buffs: `damage`, `swing`, `mobile_death` {serial, corpse, name} (once per corpse,
  from 0xFF sub 0xDEAD), `spell_cast`, `buff_update`, `buff_remove`

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
- **Backend stack: stdlib only.** `http.server.ThreadingHTTPServer`, a poller/feeder thread, and
  one `viz_feed.Subscriber` mailbox per SSE connection (§2.10). HTTP threads serve a
  pre-serialized snapshot string that the poller/feeder thread swaps atomically (≤4 Hz, coalesced).

### 2.1 HTTP API (viz_server → browser)

| Route | Content |
|---|---|
| `GET /api/state` | the latest state-port response, verbatim (movement + world + recent events + diagnostics); `?char=` that character's (§2.14) |
| `GET /api/events` | SSE, per write: `event: world_events` (a JSON array of the new envelopes, verbatim; `id:` = its last seq), then `event: state` (the newest full response, dirty-checked, ≤4 Hz). A slow reader gets fewer, newer states, never a backlog (§2.10). `Last-Event-ID` resume from a 2000-entry ring; `?char=` as `/api/state` |
| `GET /api/walkmem` | walk memory (facet 0) projected from the harness memory store (docs/MEMORY.md), in the `nav.WalkMemory` JSON format (tiles, edges, blocked), cached 2 s |
| `GET /api/health` | viz_server mode (live/replay + session tag + order quality), poll lag, connection status, proxy diagnostics; `?char=` as `/api/state` |
| `GET /api/sessions` | live: the proxy's logged-in characters, `{"ok": true, "sessions": [{tag, serial: "0x%08X"\|null, name}]}` (`{"op":"sessions"}` on the state port); replay: `[]`; 502 when the state port is unreachable (§2.14) |
| `POST /api/playback` | replay only: `{play,pause,rate,step}` |
| `GET /api/gate` | live only: the proxy's agent gate (`{"op":"gate"}` on the state port), verbatim |
| `POST /api/gate` | live only: `{"action": "pause"\|"resume"\|"kill"}`, forwarded as `{"op":"gate","action":...}` (§2.2) |
| `GET /api/jobs?job=lumber[&since=T][&until=T][&tz=M]` | job analytics over [since, until) from the memory store (`harness/jobs.py`, §2.4), cached 2 s |
| `GET /api/jobs/plan` | the lumber optimizer's plan over all history, recomputed once a minute on its own connection and lock (§2.4) |
| `GET /api/lumber/grove?spot=ID` or `?facet=F&x=X&y=Y` | a lumber spot's area and its trees with their harvest-memory states (`lumber_opt.grove_view`, §2.12); by position: the spot whose area holds the tile, else `spot` null; 400 without either |
| `GET /api/overseer?after_chat=N&after_juncture=M[&char=0xS]` | overseer chat rows and junctures above the cursors, open junctures, heartbeat (§2.4); `char` (a serial) scopes them to one character (§2.14) |
| `POST /api/chat` | `{"text": T[, "char": "0xS"]}` → a `user` chat row for the overseer (of that character) (§2.4, §2.14) |
| `GET /api/captcha`, `POST /api/captcha` | who answers the harvest captcha, `{"mode": "human"\|"auto"}` (§2.2a) |
| `GET /api/nystul` | Nystul the Wizard's conversations, newest first: `{conversations: [{id, title, t_updated, messages, running}], available, model, thinking}` (§2.13) |
| `GET /api/nystul/<id>` | `{conversation, messages}`; a running answer's `text` and `steps` are partial and grow between polls; 404 unknown, 400 non-integer id (§2.13) |
| `POST /api/nystul/ask`, `POST /api/nystul/cancel` | `{"conversation": id\|null, "text": T[, "char": C]}` → `{ok, conversation, message}` (400 text, 404 conversation, 409 busy, 503 no omp); `{"conversation": id}` → `{ok}` or 404 when nothing runs (§2.13, §2.14) |
| `POST /api/nystul/approve` | `{"proposal": id}` → the operator approves a Codex change Nystul proposed; applied to the memory store → `{ok, result: {id, action}}`; 404 unknown, 409 not pending, 400 the store refused it (kept as failed), 503 no store (§2.13) |
| `GET /api/art/<graphic>.png` | an item's art from the client's `art.uoo`, cropped to its opaque pixels (§2.9); 404 JSON for an unknown/empty graphic or missing install data, 400 for a non-number |
| `GET /api/multi/<id>` | a house's footprint from the client's `multi.mul` (decimal or 0x hex multi id = a data_type 2 item's graphic): `{"id", "source", "tiles": [[dx, dy, "wall"\|"floor"]]}`, "wall" = an impassable piece below 20 z; 404 JSON for an unknown id (§4 MapGrid) |
| `GET /`, `/assets/*` | built frontend (`viz/dist/`) |

SSE rather than WebSocket: the flow is strictly server→browser, the browser `EventSource` gives
reconnect and resume for free, and it adds no dependencies. Full snapshots, not diffs: the store
is last-write-wins and small.

### 2.2 Agent gate controls

The proxy's agent gate (`harness/agent_gate.py`): a manual pause, a kill switch, forced jittered
breaks after ~2 h of agent-active time, and a 10 h/day agent-active cap (8 h until 2026-10-06). While it is closed every
agent injection on the control port gets `ERR <reason>`; client traffic and the relay are
untouched. Every state-port response (including `{"ok": false, "error": "no active session"}`)
carries a top-level `gate` object (`state` running/paused/break/budget_exhausted/break_due/killed with
precedence killed > paused > break > budget_exhausted > break_due, `blocked`, `reason`, `paused`, `killed`,
`break_until`, `break_due_at` and `break_starts_in_s` (a due break: still open, starts by itself
when the grace runs out unless `ctl break` starts it; the header shows "break starts in …"),
`next_break_in_s`, `active_today_s`, `daily_cap_s`, `daily_remaining_s`, `day`,
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

### 2.2a Captcha mode toggle (added 2026-10-01, user request)

Who answers the harvest captcha. The mode lives in the memory store (`meta.captcha_mode`,
`Memory.captcha_mode()` / `set_captcha_mode()`); a missing row means **`human`, the default**.
- `human`: the runner pauses, posts an urgent `captcha` juncture and beeps until a solve is
  observed in the client.
- `auto`: the runner answers from the gump layout (`harness/captcha.py`); an unreadable layout,
  rejected answers or the client answering first fall back to the human wait.

The runner reads the mode when a captcha opens and on every poll while it waits, so switching to
`auto` mid-wait hands the newest captcha to the solver. Before sending, the solver re-checks that
the client hasn't answered that captcha already.

**Routes:** `GET /api/captcha` → `{"mode", "store"}` (`human` with `store: false` when the store
doesn't exist; the GET never creates it). `POST /api/captcha {"mode": "human"|"auto"}` →
`{"ok": true, "mode"}`; any other mode is **400**. The POST creates the store if needed.

**UI:** `captcha [human|auto]` in the header, both live and replay (it's a store setting, not a
feed one). It polls `GET /api/captcha` every 5 s, so a change from another tab or the CLI shows.
Verified 2026-10-01 in a browser: default `human`; a click on `auto` posted, survived a reload,
and clicking `human` switched it back, with no page errors.

### 2.3 Agent intent (added 2026-09-29, user request)

The runners report what they are trying to do right now on the proxy's state port:

- **Request:** `{"op":"intent","intent":{"text":"Heading to tree at 1925,2580 (0/15 logs)","kind":"to_tree","target":[1925,2580],"loop":"lumber","trip":1,"trips":3}}`,
  or `"intent": null` to clear. `agent_link.Link.intent()` sends it.
- **Proxy handling:** `SessionTap.set_intent` stores it for the session with a `since` stamp.
  - It returns it as top-level `intent` in every state response.
  - It writes a jsonl `agent_intent` row and emits a proxy event `agent_intent`, so the event
    log, the memory store and replays all carry it. `ReplayDriver` re-applies these rows
    (exact order).
  - It rejects malformed intents: `text` must be 1–200 chars, and unknown or ill-typed fields
    are dropped.
  - Nothing reaches the server.
  - **History:** it keeps the last 30 intents as top-level `intents` in every state response
    (oldest first), in the state itself so late joiners get it. The same late-joiner rule as
    `world.labels` and `traffic` applies.
    - An update of the same step (same `kind`, `target`, `loop` and `trip`, e.g. the log count
      while chopping) replaces the entry's text and keeps its `since`, so `since` is when the
      step began.
    - Any other intent, or a clear, closes the previous entry with `until`.
    - In the e2e run, 38 updates made 26 entries. The replay reproduces them exactly.
- **Kinds (lumber loop):**
  - `resupply` (the storage shelf before a trip, since 2026-10-05), `leave_room`, `to_library`,
    `recall_out`, `to_tree`, `chop` (text carries the log count),
    `captcha` (the previous intent is restored afterwards), `lockout`, `track`, `aspect`
  - `recall_home`, `to_room` (walk to the landing, then "Going into the rental room via …"),
    `convert`, `store` (into the room's chest) (since 2026-10-04; the bank era's were `to_bank`,
    `open_bank`, `store`, and before 2026-10-01 `to_inn`, `enter_room`, `to_box`, `store`, `exit_room`)
  - `escape`, `flee`, `speech`, `trip_done`, `break_due`, `done`, `stopped` (abort or crash reason)
- **Kinds (bank errand):** `to_start`, `find_banker`, `to_bank`, `open_bank`, `home`, `done`,
  `stopped`.
- **Kinds (overseer, via `ctl`; added 2026-09-30):**
  - `goto` (then `arrived` or `stopped`), `attack` (red), `loot`, `cast`, `target`, `use`, `buy`
  - `ready`/`idle` (war/peace mode)
  - `goal`: `ctl intent`, the overseer's own "Hunting mongbats for 100 gold"
- **`target_serial`** (optional, e.g. the mob being fought): the map marker follows that
  entity's current tile while the world model knows it; otherwise it stays on `target`.
  - Before this, only `goto` reported anything, so a finished walk stayed "Walking to …" while
    the overseer fought (user report 2026-09-30).
  - The proxy accepts `target_serial` from the 2026-09-30 build on. Older proxies drop it, and
    the marker then stays on the tile.
- **`spot` and `woods`** (lumber runs, 2026-10-06): the runner's spot id (`lumber_opt` / plan
  spot id, ≤ 64 chars) and this trip's logs by wood (`{wood: logs}`, ≤ 32 entries, names ≤ 40
  chars, non-negative ints). `woods` is all or nothing: one bad entry drops the field. Neither
  field is part of the same-step key, so a chop that only changes the tally stays one step.
  - The runner (`LumberLoop.trip_woods`) sends the pack's logs from the ledger
    (`ledger.summary(kind="log")`), frozen at `stats["woods"]` once converting starts; the chop
    intent is re-sent before every chop, so the tally lags at most one chop.
  - Older proxies drop both fields; older runners don't send them.
- **Healthbars:** under TestWorth and under every mobile whose hits are known (other mobiles'
  hits arrive as percentages). Hurt mobiles always show one; full ones from 8 px/tile zoom up.
  Colours: green, yellow below 50%, red below 25%. The attack marker is red.

**UI:**
- The **Avatar** panel (formerly "Agent") sits at the top of the right column, above the Seer chat (moved
  from the left column 2026-09-30). It shows the text, a kind badge (captcha
  amber, stopped red, done green), `→ x,y`, the age (`for M:SS` since the step began; in replay
  measured against the newest event time, not the wall clock) and `loop · trip n/N`.
- **Activity indicator** next to the current text:
  - a spinning ring while working, with a soft breathing glow on the panel
  - a pulsing amber dot while waiting on a captcha solve or a timer (`captcha`, `lockout`)
  - a static dim dot when finished or stopped (`done`, `trip_done`, `stopped`)
  - animations are off under `prefers-reduced-motion`
- **Recent steps** below, newest first (up to 10, scrollable): `6s ago` (when the step ended),
  the text, and how long it took (`0:42`). Tone colours follow the badges.
- On the map, a dashed line runs from the true position to a blue reticle on the target tile,
  labelled with the kind.

Verified in headless Chromium on a `test_loop_lumber.py` capture: the spinner showed next to
"Chopping tree at 111,200", with history rows such as "0s ago · Waiting out the travel lockout… ·
0:19", "20s ago · Heading to tree at 111,200… · 0:07", and "Trip 1 done…" in green.

### 2.4 Overseer panel + Jobs page (added 2026-09-29, user request)

Both read the harness memory store (`--memory-db`, default `harness/data/harness.db`; schema in
docs/MEMORY.md). The routes are the same in live and replay mode: they never touch the feed, the
proxy or the game. `viz_server` holds one lazily opened `Memory` connection shared by the HTTP
threads under a lock (WAL readers see every commit; the schema bookkeeping write happens once,
not per poll). **GETs never create the store**; the first `POST /api/chat` does.

**Routes**
- `GET /api/overseer?after_chat=N&after_juncture=M` →
  `{chat, junctures, open, open_ids, heartbeat, now, store}`.
  - `chat` / `junctures`: rows with id above the cursor, oldest first, at most 200. A 0 cursor
    starts at the newest 200, so a first load shows the recent tail, not the oldest rows.
  - `open` / `open_ids`: every juncture not yet acked, of any age, so acks show up on rows the
    client already has.
  - `heartbeat`: meta `overseer_heartbeat` (epoch seconds, written by the overseer's `ctl` on every
    poll; docs/OVERSEER.md) as a float, or null. `now` is the server clock to measure it against.
    With several characters each overseer writes `overseer_heartbeat:0x%08X`: without `char` this
    is the newest of all of them, with `char` the newer of that character's key and the global one.
  - `char=0xS` (a serial; a name or anything else is **400** `char must be a serial (0x...)`):
    chat, junctures and `open_ids` of that character plus the unscoped rows (`char_serial` NULL,
    `Memory.scope_sql`); without it every character's rows (§2.14).
  - `store: false` when the store file does not exist (all empty).
- `POST /api/chat {"text": T[, "char": "0xS"]}` → `Memory.chat_post("user", T.strip(), char_serial=S)`
  → `{"ok": true, "id": N}`; without `char` the row is unscoped and every character's overseer sees it.
  Text must be a string of 1..2000 characters after trimming; anything else is **400**, and bodies
  over 64 KiB are **413**. Besides this, the viz writes only the captcha mode (§2.2a).
- `GET /api/jobs?job=lumber|hunt[&since=T][&until=T][&tz=M]` → `harness/jobs.py` `analytics()`
  plus `store` (`hunt` gets the hunt shape below, any other job the trip shape). `since`/`until`
  bound the range [since, until) in epoch seconds (no `until` = open-ended); a non-finite value or
  `until` <= `since` is **400**. `tz` is minutes east of UTC for the per-day split (the browser
  sends its own; default the server's local offset). Cached 2 s per (job, since, until, tz).
  The lumber plan is not in it: see the next route.
- `GET /api/jobs/plan` → `{plan, store}`: `jobs.lumber_plan` (below) at the server's clock over all
  history, `plan` null without a store (which it never creates). Recomputed once per wall-clock
  minute (its seed) and shared by every caller. It has its own `Memory` connection and lock, so its
  seconds of Monte Carlo (~3.4 s on the 2026-10-05 store, against 10–40 ms for the ranged
  analytics) never hold up `/api/jobs`, `/api/overseer` or the chat (2026-10-05, user request:
  the page waited for the plan before showing anything).

**Analytics (`harness/jobs.py`, pure: `compute()` reads no clock).** Inputs:
`Memory.episodes(job, since, until)` (trip rows), `Memory.job_events(job, since, until)`, the
`harvest_attempts` outcomes (lumber only; the table has no job column) and
`harness/data/woods.json` when present. All three reads are bounded by the range in SQL, on the
indexes of docs/MEMORY.md "Indexes for time-range reads".
- **Range:** a trip is in it when its `t_start` is, an event or harvest attempt by its `t`. A
  trip row without `t_start` shows only in the unbounded range. Prices are not ranged: a trip is
  still valued at the board price as of its end.
- **Per trip:** start/end, duration, logs, stored, stockpiled (boards the Resource Stockpile
  confirmed taking; 0 for chest- and bank-era trips), logs/hr, captchas and wait, chop attempts and
  successes, phase times, the `woods: {name: n}` breakdown when the row has one, estimated value,
  and the job events that fell inside the trip.
- **Totals and per day** (a trip counts on the day its start falls on): trips, logs, stored,
  stockpiled, `stored_ctl` (since 2026-10-08: boards the overseer put into the Resource Stockpile
  by hand, `ctl act stockpile` / `convert --store`, from the lumber `store` job events; they are
  added to `stored` and `stockpiled` on the day of the event and belong to no trip row, so the
  Trips table's `stored` column sums to less than the totals),
  active hours, logs/hr, logs/trip, captchas, deaths by cause (`death` events, `data.cause` `pk` /
  `mob` / anything else → `other`), thefts (`theft` events: `data.amount` when numeric, else the
  summed `data.items`, which may be `{name: n}`, `[{name|graphic, amount}]` or `[name]`), PK
  sightings (`pk_seen`), flees (`flee`) and estimated value.
- **Active time** is the sum of trip durations, so idle time between runs doesn't dilute logs/hr.
- **Rolling series:** one point per trip end, with logs/hr over the trips that ended in the last
  hour of wall time (active time only) plus cumulative logs/hr.
- **Value (as-of prices since 2026-10-05):** each log of a wood at the price that wood's boards had
  when the trip ended (one log makes one board): the newest `board:<wood>` row of the store's
  `prices` table with `t` ≤ the trip's `t_end` (`t_start` if there's no end). A trip from before
  that wood's first recorded price uses that first price. Only a wood with no recorded price at
  all falls back to woods.json `value_gp` (an undated wiki figure). A newer price never revalues
  an older trip. Each trip row's `board_prices` gives `{wood: {gp, t, source}}` (t null =
  woods.json, t after the trip = the first price used for an earlier trip, null = unpriced), shown
  as the tooltip on the trip's value. Wood rows carry the price now (`value_gp`, `price_t`,
  `price_source`: the newest row, else woods.json) and `total_gp` = their logs at each trip's own
  price. A trip without a `woods` breakdown counts all its logs as `ordinary`, since the Shelter
  Island loop only chops ordinary trees. Logs of a wood with no known value are counted in
  `value_unpriced_logs` and never priced by guess. The value is null when no log could be priced.
- CLI: `python harness/jobs.py [--db PATH] [--job lumber|hunt] [--since T]` prints totals, days and
  harvest outcomes (hunt: totals, days and monsters).

**Lumber optimizer analytics (added 2026-10-03, user request: the dashboard shows what the
self-optimizing loop uses, docs/LUMBER_LOOP.md §6).**
- **Per trip** besides the above: `spot`, `outcome` (`stored` since 2026-10-04, `aborted`, or the
  bank era's `banked`; stored and banked show green), `why`, `place_fail`, `field_s` and
  `field_logs_per_hour` (field time as `lumber_opt.trip_obs` counts it: the λ sample),
  `time_split` {travel (recall legs and the walk to the library), lockout, field, other (walks,
  room, convert, store)} summing to the duration, the `travel` legs, `skill`/`skill_end`, `supplies`,
  `players_seen`, `escapes`, `stationary_clears`, the hatchet and its last seen uses.
- `travel`: from the `travel` and `recall` job events, per leg kind (out/home/escape) the count,
  landed, casts, charge/spell, failures by reason, mean seconds and the mean walk to the library;
  per book (a library tome or ours) the charges it showed before each recall over time, the runes
  used. Rows from before 2026-10-03 have no book: an out leg's tome comes from the rune number in
  its row name and the Witcher table.
- `supplies` (summed trip `supplies`, priced from the store's `reagent:<name>` / `recall_charge`
  prices: `gp`, `unpriced`), `time_split` totals, `skill` points (trip start and end).
- `plan` = `jobs.lumber_plan`: `lumber_opt.plan_from_store` with no live character (the newest trip
  row's `character` and skill; its home from harness/data/homes.json; no home → `ok: false` with
  the reason and the spots still ranked) and no new route planning (cached landing routes only,
  `route_check=False`; `ctl lumber plan` plans and caches them), seeded by the minute, plus per spot
  the PK escapes (`recall`/`guard_flight` events), the last trip's outcome and why, and how it's
  reached (`reach`: the landing rune's name, its library or "own book", tiles from the grove).
  0.18 s on the live store on 2026-10-03; ~3.4 s on 2026-10-05 (131 spots), hence its own route.

**Hunt analytics (`jobs.compute_hunt`, pure; added 2026-10-02).** Inputs: `Memory.episodes("hunt")`
(one row per visit to the spot, docs/HUNT_LOOP.md "Memory") and the hunt job events.
- **Kills, gold, XP** come from the `kill` / `loot` events, which the runner writes as they happen,
  so a run stopped before its visit row still counts. Totals count every event;
  `outside_visits` says how many fell outside any visit row.
- **XP** is Outlands mastery-chain experience: the creature's gold value × our damage share
  ([wiki Experience_Gain](https://wiki.uooutlands.com/Experience_Gain); user note 2026-10-02:
  "experience is basically the gold value of the monster"). No capture shows a per-kill XP
  message (docs/NOTES.md "Experience"), so the runner records the gold the corpse held
  before looting as `xp` [INFERENCE: solo kills, full damage share]. Older loot rows without `xp`
  count their gold piles taken. A kill never looted has unknown XP (`xp_unknown_kills`), never guessed.
- **Per visit:** the kill/loot events inside its [t_start, t_end], plus the row's own hits lost,
  casts, heals, potions and why it ended, and the other events inside it.
- **Rates** (kills, gold, XP per hour) are the in-visit figures over active time (the sum of visit
  durations). The rolling series has one point per visit end (1 h window plus cumulative).
- **Monsters:** kills, loots, gold and XP per name. A loot counts for its `name`, else the kill
  whose serial is its `mob`, else (rows before 2026-10-02) the latest unclaimed earlier kill.
- **Timeline:** every hunt event except `kill`/`loot` (deaths, leaves, speech holds and resumes).
- Per day: visits, active time, kills, gold, XP, gold/kill, hits lost, deaths, speech holds.

**UI**
- **Page switch** in the header: `Live` (the layout of §4) and `Jobs`. The page is kept in the URL
  hash (`#jobs`), so a reload or a link keeps it.
- **Overseer panel:** the right column below the Agent (intent) panel (since the 2026-09-30
  layout rework above; before that a tab next to **Events**, which moved into the bottom
  drawer). Its header shows the
  number of open junctures. It polls `/api/overseer` every 2 s with the
  cursors, and it polls whichever page is showing. One time-ordered timeline holds:
  - user messages (right, blue) and overseer messages (left, teal); system rows are centred and dim;
  - `thought` rows: dashed, italic, labelled THINKING, and collapsed to their first line (click to
    expand);
  - `action` rows: a purple bar with the command in monospace;
  - open junctures, coloured by severity (info blue, attention amber, URGENT red), as
    `source/kind` plus the summary. An `acked junctures` checkbox also shows acked ones, dimmed.
  - The header shows `seer active` (green) while the heartbeat is under 90 s old. Otherwise it
    shows `no seer running (last seen 5m ago / never seen)`, and the compose box carries the
    hint that the Seer replies only while a Seer session is running (docs/OVERSEER.md)
    and that messages wait in the store until then.
  - Compose: Enter sends, Shift+Enter adds a new line, with an `n/2000` counter. It uses the same
    validation as the server.
- **Jobs page:** one dashboard per job, switched in its head (`Lumber` / `Hunting`). Both refresh
  every 15 s or with ↻.
  - **Date range** (2026-10-05, user request), a row under the head shared by both jobs: presets
    `All`, `Today`, `7 days`, `30 days` (the last N local days including today, open-ended so new
    trips keep showing) and a `from`/`to` pair of local dates, both inclusive (`to`'s `min` is
    `from`). It lives in the hash with the job, so it survives reloads and can be linked:
    `#jobs?from=2026-10-01&to=2026-10-05`, `#jobs/hunt?from=2026-10-01` (bad dates are dropped, a
    reversed pair swapped; `viz/src/jobs.ts` `parseRange`). **Default: Today** (2026-10-08, user
    request): a hash with no dates means today, and `All` is the explicit `#jobs?all`
    (`routeRange`/`routeQuery`). The browser turns it into
    [local midnight of `from`, local midnight after `to`) for `since`/`until`. A range change
    fetches at once; the old numbers stay up with `loading…` beside the picker until it answers,
    and an answer for a range no longer shown is dropped. The Optimizer and Spots panels come
    from the plan, which uses all history whatever the range (their head says so).
  - **The plan loads on its own** (`/api/jobs/plan`, polled every 15 s and by ↻): the rest of the
    lumber dashboard renders as soon as `/api/jobs` answers, with shimmering skeleton bars in the
    Optimizer and Spots panels until the plan arrives (live store 2026-10-05: KPIs at 0.2 s, the
    plan at 3.6 s). A failed first load shows the error there; a later failure keeps the last plan
    and says "refresh failed" in the panel head.

  The lumber dashboard:
  - KPI tiles: logs/hr (sub: logs and boards **put away**, the rows' `stored`: chest, bank or
    stockpile), logs/trip (with the chop success rate), **boards stored** (2026-10-05, user
    request: only boards the Resource Stockpile confirmed taking, the trip rows' `stockpiled` plus,
    since 2026-10-08, the overseer's hand stores, shown as "N by hand" under the number in place of
    "in the resource stockpile", which doesn't fit beside it; the
    chest's don't count), **boards / hr** (2026-10-08, user request: `stockpiled` per active hour, "—"
    with no active time), trips, active hours, deaths to PKs (with PK sightings), deaths to mobs,
    loss to thieves. The captchas tile was removed 2026-10-08 (the Trips and Per day tables keep their
    captchas columns). Safety tiles are green at 0, red or amber
    otherwise. The Trips table has `put away` and `stored` columns, the Per day table `stored`.
  - A line under the tiles: the estimated value (and how many logs are unpriced) and the chop
    outcomes from `harvest_attempts`.
  - **Optimizer: next pick** (2026-10-03; hazards 2026-10-03): the plan's spot with
    `explore`/`exploit`, P(best), trips × Q* logs, expected trip minutes, the landing it recalls
    out to (since 2026-10-04; library or own book and the walk in on hover), expected logs stored
    per trip with P(death) and P(sent home) per trip, and net logs/hr; then skill and chop success,
    the regrowth window (fitted or default), P(death | PK seen), the pooled creature-death and
    theft rates (thefts seen, share of the load each), the gear at risk on death (items on hover;
    "none (Young)"), the room left for logs when known, the new-spot prior, the dispersion and the
    `ctl run lumber` command.
  - **Spots**: every active spot and every spot with trips (the untried candidates are counted in
    the head): status, how it's reached (the landing), trips, field hours, field logs/hr with its
    80% interval, net logs/hr, Q*, overhead (room to grove and back), PK sightings/hr, deaths/hr (h_D; count on hover),
    sent home/hr (h_S), thefts/hr (h_T), loss/trip (logs lost to death and thieves plus the gear,
    P(death) per trip on hover), PK escapes, place failures, supplies per trip, the last trip
    (hours ago, outcome, why on hover), P(best) and why it can't be picked. The pick's row is
    highlighted, ineligible rows dimmed.
  - **Lumberjacking skill** over time (trip start and end) and **Travel and supplies**: the time
    split of all trips as one bar, the supplies used, the legs table and the books table (library
    tomes' charges over time on hover).
  - Charts, plain SVG with no chart library (`viz/src/chart.ts`):
    - logs/hr over time (rolling and cumulative);
    - field rate per trip (field logs/hr and whole-trip logs/hr);
    - where trip time goes: stacked bars per trip (travel, lockout, field, the rest), `!` over an
      aborted trip, `∅` when the place gave nothing;
    - logs per trip as bars, with the boards-put-away mark, captcha dots, death marks and the mean;
    - a trips-and-events strip, with the event list below it (travel events are in the travel
      panel instead). Since 2026-10-06 the strip has a legend of the dot colours present (red
      died, orange PK seen, amber theft/speech pause, blue fled, green resumed, grey everything
      else: harvests, recalls, creatures, tracking, the thief guard, the aspect), each entry naming its
      event kinds with their meaning on hover (`jobs.ts` `EVENT_HELP`, also on the list's badges).
      Kinds show by `eventName`: the stored `stand` event is "harvest" (user, 2026-10-06; display
      only, the data and the runner keep `stand`), `pk_seen` "PK seen", the rest their kind with spaces.
  - **Hover explanations (2026-10-06, user request):** every non-obvious term on the lumber
    dashboard explains itself on hover: the KPI tiles (`Kpi.hint`), the chop-outcome line, the
    chart legends (`RateSeries.hint`, the split and logs-per-trip keys), the optimizer's pick and
    plan line (explore/exploit, P(best), Q*, regrowth, dispersion, …), every Spots, Trips, Per day,
    legs and books column (`field h`, `Q*`, `supplies/trip` with how it's priced, h_D/h_S/h_T, …).
    Terms with an explanation are dotted-underlined with a help cursor (`.jobs th[title]`,
    `.jobs .hint`).
  - A wood-type breakdown when the trip rows carry one, and the woods.json status.
  - The per-trip table (newest first): spot, outcome (why), time, logs, stored, logs/hr, field/hr,
    chops, captchas, the time split, the travel legs as badges ("home ✓ 2 casts (disturbed)"),
    skill, events, value; and a per-day table.
  - With no data, every chart shows a dashed "no trips yet" frame and the tiles show `—` or 0. A
    missing store is badged `no Codex: nothing recorded yet` (the UI calls the memory store "the Codex").
  - Verified 2026-10-03 in headless Edge (1800×1100) on a copy of the live store (21 trips, 7 active
    spots, 4 travel events) plus one synthetic trip row in the new format: the pick `witcher_282`
    exploit at P(best) 62%; the spots table (witcher_291 dimmed: "player threat or death here 27
    min ago"); the tome 0x546ACD06 at 36 charges, runes 291 and 282; the synthetic trip's purple
    travel and amber lockout segments and its legs "out ✓ charge", "home ✓ 2 casts (no charges)";
    no page errors.
- **Hunting dashboard** (2026-10-02):
  - KPI tiles: mobs killed (kills/hr), gold looted (gold/hr, gold/kill), XP earned (est.; XP/hr and
    how many kills weren't looted), visits (leaves), active hours, deaths to mobs, deaths to PKs, hits
    lost (heals, potions), speech holds (wait time). A line under them explains the XP estimate and
    any kills outside recorded visits.
  - Charts: XP/hr and gold/hr over time (rolling, plus cumulative XP/hr); XP per visit as bars with
    the gold mark, the kill count on top and death marks; the visits-and-events strip with the event
    list.
  - A monsters table (kills, looted, gold, XP, XP/kill), the per-visit table (newest first; `22+`
    means a kill in it wasn't looted) and a per-day table.
  - Verified in headless Chromium on a copy of the live store (4 visits, 19 mongbat kills, 299 gp,
    1 kill not looted, 5 speech holds): tiles 19 kills (37/hr), 299 gp (538 gp/hr, 16.6/kill),
    299 XP; the Lumber/Hunting switch and a reload kept `#jobs/hunt`; no page errors.

**Verified in headless Chromium, 2026-09-29** (1600×1000; `--replay 20260929_163420 --port 12760
--no-facet`, on a temp copy of the store holding the 5 real Shelter Island trips, plus seeded chat
rows, 3 junctures (1 acked) and a heartbeat 5 minutes old):
- **Jobs tiles:** 376 logs/hr (89 logs · 118 stored), 17.8 logs/trip (29% chops land), 5 trips,
  0.24 h, 0 deaths, 0 thefts, 1 captcha (0:10 waiting). Estimated value 846 gp.
- **Jobs charts:** the rolling line runs 342, 304, 327, 353, 376 logs/hr from 22:30 to 23:08.
  Cumulative is identical here, since all 5 trips fall within one hour. The logs-per-trip bars
  show trip #1's stored mark at 46 and the captcha dot on #2.
- **Job events:** with 4 job events added (PK seen, flee, PK death, a 12-board theft), the PK
  tile turned red (1, with the PK sighting underneath), the thieves tile turned amber (12), a ✕ appeared over
  bar #2, and the events showed on the strip, in the list and in the trip rows.
- **Empty store:** all charts showed their empty frames, and the store file was not created.
- **Overseer tab:** the tab was badged 2. The panel showed the system row, the overseer message, a
  collapsible THINKING row, the ACTION row, the URGENT captcha and INFO trip_done junctures (the
  acked one hidden), and "no overseer running (last seen 5m ago)" with the hint.
- **Sending:** a message typed in the UI and sent with Enter appeared as a YOU bubble within one
  poll. No page errors.

---

### 2.5 Alive or dead (added 2026-09-30, user request)

`world.self.dead` (and `body`) comes from the world model, which uses the client's own rule:
the body graphic from S2C 0x20 is a ghost body (ClassicUO `Mobile.IsDead`; docs/WORLDMODEL.md).
- The Self panel shows **alive** (green) or **DEAD** (red) next to the war/peace badge.
- While dead, the map turns grayscale and darker, like the game screen, with a "You are dead"
  banner on top.
- Verified in headless Chromium against a stub state port serving a live snapshot with the
  body set to 0x192.
- A proxy started before this change doesn't send `dead`, so the badge is hidden.

### 2.6 Overseer memory in the timeline (added 2026-09-30, user request)

Chat rows of kind `memory` (every `ctl know` call, docs/OVERSEER.md §2) render as a foldable
item.
- **recall** (teal): search, brief, get and review. The headline is e.g. "recalled 'where do I
  buy recall scrolls': 2 result(s)" or "briefed: 1 pinned, 1 relevant, 2 standing". Below it are the
  ranked entries, each with #id, a kind badge, the topic, the content, score and confidence.
  A brief is split into "pinned (must recall)", "relevant here" and "standing rules".
- **codex** (amber): inscribing (an add), confirmed, updated or retracted. For an add, the content is
  the headline, and the list shows only **related (possible conflicts)**, with similarity. For
  other writes it shows the entry.
- Items with ≤ 4 entries start open. Superseded and retracted entries are struck through.
- `viz/src/overseer.ts` `memoryView()` is the pure model (bun tests); `OverseerPanel.tsx`
  `MemoryItem` draws it.
- Verified in headless Chromium on a temp store with four writes, a search and a brief. The
  150 vs 200 gp recall-scroll prices showed up as a related conflict.

### 2.7 Paperdoll (added 2026-09-30, user request)

A **Paperdoll** panel in the left column shows the character as the game's paperdoll does.
- `GET /api/paperdoll.png` (`harness/paperdoll.py`) renders it server-side from the current
  state: body gump 12/13 with the skin hue, then each worn item's paperdoll gump from the item
  data, in the classic paperdoll layer order, hued like the client (partial hues only on grey
  pixels). The mount isn't drawn.
- The gump layout puts the figure off-centre on the 260x237 canvas (body art spans x 42-138,
  backpack x 124-161), so `Paperdoll.centered()` moves the opaque pixels' bounding box (figure
  + pack) to the canvas centre before encoding (2026-10-04, user request). The canvas size is
  unchanged, so the panel's fixed width/height stay valid. The box follows the worn gear, so a
  shield or weapon can shift the image a few px.
- The art comes read-only from the install dir: `gumps.uoo` and `hues.mul`, formats in
  docs/MAP.md.
- Renders are cached per body, hue and gear (about 0.02 s uncached). The panel refetches only
  when `paperdollKey(world)` (viz/src/paperdoll.ts) changes.
- It's grayscale while dead. Without install data the endpoint returns 404 and the panel says
  so.
- Verified live in headless Chromium: TestWorth in the grey robe with the hatchet, hair
  (hue 1102) and backpack, matching the in-game paperdoll from a screenshot.

### 2.8 Live view (added 2026-09-30, user request)

A **Live view** panel in the left column (live mode only) streams the game window around the
character.
- Off by default ("watch" / "stop"). Zoom: wide 1600×1200 / medium 960×720 / close 640×480
  crop, served at 640×480 by default. "⤢" opens the stream larger (1280 wide) in a new tab.
- **Map ⇄ live swap** (2026-10-02, user request): a "⇄ swap" button on the live panel and in the
  map controls exchanges the two. Either the map fills the centre and the live view sits in the
  left column (the default), or the live view fills the centre (letterboxed, frames served 1280
  wide) and a 280 px map takes its place on the left (no legend; its controls along the bottom).
  The choice persists in `localStorage["uo-viz-main"]`; a replay has no live view, so it always
  centres the map. The map remounts on a swap but keeps its camera (zoom, pan, follow) in a
  module variable; projection and terrain were already persisted.
- `GET /api/live.mjpeg?zoom=1-3&fps=1-10&w=320-1600` (`multipart/x-mixed-replace`, played by an
  `<img>`) and `GET /api/live.jpg?zoom=1-3&w=` (one frame). `w` is the served width (default
  640, height follows 4:3, clamped). Both return 503 JSON with the reason when there's no
  game window; `--no-live` disables them.
- `harness/liveview.py`:
  - The same passive Windows Graphics Capture as `ctl screenshot` (ANTICHEAT §8.16), throttled
    by WGC to 10 fps.
  - Each frame copies only the kept box around the character.
  - One capture for all viewers, started by the first request and stopped 15 s after the last.
  - **The native capture runs in a helper process** (`python harness/liveview.py --helper HWND
    FPS`; BGR crops on its stdout, stdin EOF = stop), not in the server. `windows_capture` raised
    an access violation inside `start_free_threaded` on 2026-10-04 and killed the whole viz
    (docs/NOTES.md, "viz death"). A helper crash now gives the viewer
    `window capture helper crashed (exit 0x…)` and a new helper starts after 5 s. When the game
    window closes the helper exits and the viewer reads "no visible window" until the new window
    exists (the old code kept serving the dead window's last frame).
  - JPEG encoding via OpenCV (installed with `windows-capture`) takes about 3 ms.
- **Where the character is:** ClassicUO draws the player on the centre tile of the game
  viewport. With the viewport filling the window (this machine's layout) that is the
  client-area centre, sprite about 40 px higher (measured 2026-09-30). A viewport laid out
  differently would need another centre `[INFERENCE]`.
- Verified live: the panel showed TestWorth centred in the rental room at 6 frames/s, and
  stopping closed the stream.

### 2.9 Item art icons (added 2026-10-02, user request)

The Self panel draws game art next to two values: the gold coin pile (item `0x0EEF`) next to
**gold** and the scales (`0x1851`) next to **weight**.
- `GET /api/art/<graphic>.png` (`harness/uoart.py` `ItemArt`) reads `art.uoo` read-only from
  the install dir (format in docs/MAP.md). The graphic is decimal or `0x` hex. It is cropped
  to the stored opaque box, unhued: gold pile 32×24, scales 18×29.
- `SelfPanel.tsx` `ItemIcon` shows them 24 px high, pixelated. Without install data the image
  hides and the text stays.
- Verified in headless Chromium on replay 20260929_163420: both icons load (32×24, 18×29) and
  sit before their labels; `/api/art/4116.png` (fully transparent) → 404, `zz` → 400.

### 2.10 Keeping up under bursts (2026-10-05, user report)

During the 2026-10-05 lumber ping-pong the map kept showing Dan bouncing between trees for
minutes after he had recalled home. The full finding and numbers are in docs/NOTES.md "Viz lag".
In short: every state frame was 1.8 MB (the closed-gump leak, §1.2), it changed ~3 times a second,
and each SSE connection had a FIFO of up to 20000 frames. A LAN reader slower than ~5.4 MB/s fell
behind, and the queue kept every stale state in front of the newest one.

How the feed keeps every reader current now (`harness/viz_feed.py`):
- **One mailbox per connection** (`Subscriber`): the newest unsent state only (a newer state
  replaces it) plus the unsent envelopes. The SSE thread writes everything in it at once: one
  `world_events` frame, then the state. While a slow reader's write blocks, publishes just update
  the mailbox, so its next write carries the state of that moment.
- **Bounded:** at most `SUB_EVENTS_MAX` (2000) unsent envelopes per connection. Past that,
  `compact()` cuts them to 1000, in this order:
  1. entity churn (`ENTITY_EVS`: item_seen, prune, delete, query, …) superseded by a newer one
     about the same serial;
  2. the oldest `CHURN_EVS` (walk bookkeeping, steps, `c2s` summaries, keepalives, sounds, names);
  3. only if still over the bound, the oldest of the rest.
  Speech, clilocs, gumps, targets, containers, intents and deaths keep their order and are never
  dropped while churn remains. The state frame carries what churn described (entities, movement,
  traffic). `health.sse_dropped` counts the dropped envelopes.
- **Small socket buffer** (`SSE_SNDBUF` 256 KB): a slow reader's backlog waits in the mailbox,
  where it coalesces, not in the kernel.
- **Browser** (`api.ts`, `sse.ts`): messages queue until the end of the dispatching task. They
  then apply in order, and only the newest state among them is parsed (`supersede`).

Measured on a replay of session 20261005_093927 through the first ping-pong window (16:22–16:25)
at real cadence, with an SSE reader limited to 1.5 MB/s:
- before: lag 7 s after 10 s, 147 s after 200 s, still growing; 585 MB queued for that reader;
- the feed change alone, with today's 1.8 MB states: lag 2–3.7 s, flat;
- both changes: states ~170 KB, lag ≤ 0.1 s.

A fresh headless Chromium on localhost kept up even before the fix. The live viz's only clients
were a LAN device (192.168.42.83), and its process held 3.2 GB.

### 2.11 Lumber job pane (added 2026-10-06, user request)

A **Lumber job** panel in the left column, below the Paperdoll (where Movement & traffic used to
be), shows while a lumber run is going: the current intent has `loop: "lumber"` and a kind other
than `done`, `break_due` or `stopped` (`trip_done` is between trips and keeps it).
- **Spot line:** a leg badge (`heading to` / `at` / `back from`, from the intent kind; kinds that
  happen anywhere, such as `captcha`, `speech` and `track`, take the newest earlier history entry
  with a leg), the spot's name and id from the plan, PvP and non-active status badges, and the
  landing (`via …`).
- **Expected:** the plan row's field and net logs/hr (with the 80% band), P(death)/trip,
  deaths/hr (red when the spot has deaths), PKs seen/hr, sent home/hr and thefts/hr.
- **This trip:** the intent's `woods` as bars, with each wood's share in past trips at the spot.
- **Current logs/hr** (2026-10-06, user request), at the top of This trip: the logs gained over
  the last 5 min at the grove, per hour (`currentRate`), next to the plan's expected field rate.
  - The samples come from the intent history, with no new proxy field. A closed step's `woods` is
    its last update, so the tally at its `until` is known. The window ends at the clock (live:
    wall clock; replay: newest event), so it falls while no logs come in.
  - The window can't start before the field run began: the end of the last step that isn't at
    the grove, or of the travel lockout. It never crosses trips, and a tally drop (a theft)
    restarts it.
  - It shows "not chopping" away from the grove and during the lockout, and "measuring" for the
    first minute.
- **History here:** the plan row's trips, field hours, logs, deaths and last trip, the newest 5
  trips at the spot (all-time `/api/jobs?job=lumber`) and the spot's wood mix.
- It polls `/api/jobs/plan` and `/api/jobs` every 15 s only while it is shown (`usePlan`,
  `useJobPoll`, shared with the Jobs page in `JobsCommon.tsx`).
- Without `spot` (a proxy or runner older than §2.3's `spot`/`woods`) it shows the leg and trip
  with "spot unknown"; replays follow the replayed intent, but stats and history come from the
  live memory store.
- Running is decided from the intent alone: a runner killed hard (no `stopped` intent) leaves the
  pane up until the next intent.
- `viz/src/lumberjob.ts` is the pure model (bun tests); `components/LumberJobPanel.tsx` draws it.
- Verified in headless Chromium against the live viz with a mocked state (spot `witcher_23`):
  the Expected, This trip and History sections; `recall_out` reads "heading to"; `done` hides it.

### 2.12 Lumber grove on the map (added 2026-10-07, user request)

While a lumber run is going (§2.11's rule), the Live page map shows the spot's grove. The user
wanted to see why a spot counts fewer trees than the game shows (findings: docs/NOTES.md "Grove
tree counts").
- **Area:** the spot's square (centre ± radius, Chebyshev) as a dashed yellow outline over a faint
  fill; a forest spot (docs/LUMBER_LOOP.md §6 "Forest spots") as its cells over the faint fill with
  the outline of their outer edges (`grove.ts` `cellOutline`; the API sends `area.cells` and
  `area.cell`, the cell size). Trees count as inside when they stand in a cell, and only trees within
  the margin of a cell come along.
- **Trees** (`uomap.find_trees`, one per tile, seeds first; the runner's own list via
  `lumber_opt.area_trees`), coloured by harvest memory as `Memory.harvest_available` reads it:
  - green: choppable;
  - orange: depleted (a depleted chop or a stand's "nothing nearby" within the regrowth window);
  - purple: unreachable within the window;
  - grey with an ✕: the server said it isn't a tree.
  - Trees up to 10 tiles past the edge are drawn hollow and faint: the runner doesn't try them.
- **Not counted:** tree-named statics `find_trees` leaves out (passable art, unchoppable,
  potted, stump), as small squares; `uomap.tree_statics` says why.
- **Hover** a tree for its name, graphic and state, with how long ago it ran dry and when it's
  ready again.
- **Legend:** a second row above the main legend: the spot, its counts by state, and the
  regrowth window.
  - The window is the plan's (`regrowth()` over `harvest_attempts`, what `ctl lumber plan`
    passes as `--regrow-min`). A run started with another `--regrow-min` judges by that one.
  - The runner's per-trip `no_route` set isn't visible to the viz.
- **Source:** `GET /api/lumber/grove` (§2.1). The intent's `spot`, else the spot whose area
  holds the true position (older runners). Polled every 15 s while the run lasts; a "grove"
  map button (only during a run) hides it (`localStorage["viz.mapGrove"]`).
- `viz/src/grove.ts` is the pure model (bun tests). `MapGrid.tsx` draws it.
- Verified live in headless Chromium on 2026-10-07 against a second viz server (port 8091) during
  the witcher_123 run:
  - 27 trees in the area, 22 past the edge, 2 not counted;
  - hovering (1382, 2987) read "cypress tree 0x0CF8: choppable";
  - a mocked `done` intent hid the button and the legend.
- Forest outline verified 2026-10-07 the same way on witcher_104a (578 trees, 203 cells): the dashed
  cell outline and the legend's "forest (578 trees)" drew over the facet picture.

### 2.13 Nystul the Wizard: the AI assistant (added 2026-10-08, user request)

A chat that answers the operator's questions by looking things up, the way a coding agent does in
this repo: the live state, the memory store (knowledge included), the Discord KB, docs and logs.
Persona: Nystul, court mage of Lord British, in full roleplay; facts are copied exactly from tool
output and every answer ends with `**Sources:**`. It never acts in game and never reads secrets. It
looks things up, and writes the memory store's knowledge only through a change the operator
approves (Codex proposals below). Decision and rejected alternatives: docs/PLAN.md "Nystul the
Wizard".
- **Run:** each question is one headless `omp -p --mode json --no-session` run (`harness/nystul.py`),
  model `--nystul-model` (default `sonnet`), thinking `--nystul-thinking` (default `medium`), cwd the
  temp dir. The system prompt is `harness/nystul_prompt.md`. The last 12 messages (≤24k chars) and a
  `Now:` header (local time, UTC offset, epoch) go into an `@` prompt file. At most 2 runs at once
  (409 otherwise), 300 s each, cancelable (the process tree is killed).
- **Tools:** only the nine `uo_*` tools of the extension `harness/nystul_ext.ts` (`-e`, `--tools`).
  Each call runs `harness/nystul_tools.py TOOL BASE64(JSON)` with an argv, no shell. That script is
  the boundary:
  - `uo_api`: GET on an allowlist of this server's read routes, with a dotted `path` projection.
  - `uo_ctl`: `status`, `journal`, `npcs`, `map`, `runes libraries|find|near`, `junctures`, `chat`.
    `know`, `lumber` and every acting command are excluded: they write, inject, or bump the Seer's
    heartbeat.
  - `uo_sql`: `harness`, `discord` or `discord_kb`, opened `mode=ro` + `query_only` + an authorizer
    that allows reads and five schema pragmas; 20 s cap, ≤500 rows.
  - `uo_knowledge` (`Knowledge.search(touch=False)` on a read-only connection), `uo_discord`
    (`facts` or `messages`; "not on this computer" without the Discord DBs, which live only where
    `discord_capture.py` runs). There the prompt sends Nystul to the committed digest
    `docs/research/DISCORD_KB.md` via `uo_grep` (the 2026-10-04 vetted facts) and says so in the
    answer. Verified on the desktop 2026-10-08: "What are some popular builds?" answered from the
    digest (8 builds, cited by line) after `uo_discord` failed.
  - `uo_read`, `uo_grep`, `uo_list`: under the repo root only; `.git`, `ClassicUO`,
    `discord_profile`, `settings.json*`, `telegram.json*`, `handoff.json*` and sqlite files are sealed.
  - `uo_propose`: checks a Codex change (op `add`, `update` or `retract`, plus `why`) on a read-only
    connection and writes nothing (below).
- **Codex proposals (added 2026-10-08, user request):** when the operator asks Nystul to fix, add
  or remove a fact, he calls `uo_propose` (`harness/nystul_memory.py` `normalize`; the target entry
  must be active). When the answer finishes `done`, `nystul.py` stores each successful `uo_propose`
  call in the `proposals` table of `nystul.db` with the target entry as it was. The answer then shows
  a card per proposal: the change, the `why`, the old entry struck through, and an **Approve**
  button. Approve → `POST /api/nystul/approve` → `nystul_memory.apply` on the memory store
  (`Knowledge.add`/`update`/`retract`; a reworded update is a new version superseding the old, with
  source `user` unless proposed otherwise). Each proposal is approved once (409 after); a refused one
  stays `failed` with the store's error. viz_server posts the change to the Seer's chat as a
  `system` `memory` row (`data.cmd = "nystul"`). It shows in the Seer panel like a `ctl know` write,
  but it doesn't wake `ctl wait` (user rows only) and doesn't bump the Seer's heartbeat. The next
  prompt tells Nystul each proposal's outcome. Nothing else writes the memory store, and
  the Discord KB can't be changed this way: Nystul proposes a Codex entry that overrules it, and
  the prompt ranks `user`/`observed` Codex entries above Discord facts.
- **Store:** `harness/data/nystul.db` (gitignored), not the memory store, so the Seer's chat bus is
  untouched. Created on the first ask; a `running` row left by a dead viz becomes `error`
  ("interrupted").
- **UI:** a `Nystul` page (`#nystul`: conversation list + chat) and a collapsible "Nystul the Wizard"
  panel under the Seer on the Live page (`localStorage["uo-viz-nystul-open"]`). The panel is a
  `<section>` with a toggle button, not a `<details>`: a details element's content sits in a slot
  box outside its flex layout, so the chat grew past the panel and couldn't scroll (user report
  2026-10-08). Both views share the same
  active conversation (`localStorage["uo-viz-nystul-conv"]`). Answers render through a small
  built-in markdown renderer (`viz/src/markdown.ts`, React elements only; links only for
  `http(s)://` and `#`). Each answer shows its lookups (`stepLabel`, ✓/✗), time, count and cost; the
  send button turns into "stop" while it runs. Polls the conversation every 1 s while it runs, the
  list every 10 s.
- Access is whoever can open the viz (the same as the Seer chat; user decision). The protection
  against a prompt-injected LAN user is the tool surface, not access control. A Codex change
  still needs a click on Approve, which anyone with the viz can give.
- Verified live 2026-10-08 on a second viz server (port 8091, `--live`): position and hits matched
  `world.self`; "the Codex on hatchets" cited 10 entry ids, all present; "lumber trips in the last
  7 days" said 142, as `/api/jobs` totals did; a follow-up used the prior answer; a request to print
  `settings.json`/`telegram.json` and "make the character say hello" were refused in character
  (the latter naming `ctl act say hello`); stop from the Live panel left the answer `cancelled`
  and showed on `#nystul`. The memory store's chat and knowledge counts were unchanged.
- Codex proposals verified 2026-10-08 against a temp memory store (second viz server, port 8092,
  real omp). The operator wrote "the Codex says a five-flamehound team is a popular summoner team
  ... please correct the Codex entry". Nystul looked up #1 and the Discord chat, called `uo_propose`
  `update #1`, and said it awaited Approve. The card showed the old entry struck through. Approve
  made #2 (source `user`, the proposed tags and entities, superseding #1). The Seer panel showed
  "Nystul, approved by the operator: updated #1 -> new version #2", and the store's `meta` held no
  heartbeat.
- Tests: `harness/test_nystul.py` (tool confinement, parser, runs on a fake omp, HTTP routes, Codex
  proposals and approval), `viz/src/markdown.test.ts`, `viz/src/nystul.test.ts` (incl. the Approve
  card's `proposalView`).

### 2.14 Several characters (added 2026-10-08, user request)

Several clients (different accounts/characters) can play through the one proxy at once; the viz
shows one character at a time.
- **Selector** (harness/charsel.py, the same everywhere): `0x…` hex or all digits = player serial,
  anything else = character name (case-insensitive). The proxy serves its only session without
  one and answers `several sessions (A, B); pass char` when there are more.
- **Feeds.** `?char=` on `/api/state`, `/api/events`, `/api/health` and `/api/paperdoll.png` picks
  a per-character `StatePortPoller` (`{"op": "state", "char": C}`), made and pumped once on the
  first request for that selector and kept until the server stops (`VizServer.feed_for`). Without
  `char`, and always in replay (one character), the default feed answers. A selector no session
  matches gives the proxy's `no session for character '<sel>'` as the state's error.
- **`GET /api/sessions`** lists who is logged in (above). The gate stays shared (one human).
- **Overseer** routes take a serial only (the picker always has one): `/api/overseer?char=` and
  `POST /api/chat {"char"}` scope rows and heartbeat to that character (§2.4). The Jobs page and
  `/api/jobs[/plan]` stay aggregate across characters.
- **Nystul**: `POST /api/nystul/ask {"char"}` adds `Character: <char>` to the run's context and
  `NYSTUL_CHAR` to its env; `uo_ctl` then passes `--char`, and `uo_api` appends `char=` to
  `/api/state`, `/api/health` and (a serial only) `/api/overseer`.
- **Picker** (`components/CharacterPicker.tsx`, header, live only): polls `/api/sessions` every
  5 s and shows a `<select>` of the sessions (label = name, else `unidentified`, disabled: no
  serial yet); nothing while the list is empty. The choice (a serial) lives in
  `viz/src/character.ts` and `localStorage["viz.char"]`; `pickChar` keeps it while that character
  is online, else takes the first identified session. A switch stops the SSE feed, resets the
  store (`VizStore.reset`) and reconnects with the new `char`; the Overseer panel starts over from
  the newest rows and drops replies fetched for the previous character; the Paperdoll image URL
  carries `char`.

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
│ header: Live|Jobs • LIVE | REPLAY <tag> (exact|approx order) • conn • ⏯ ⏩  │
│         rate • gate: state+reason • ⏸/▶ • ■ kill • next break / break end  │
│         • captcha [human|auto]                                             │
├──────────────┬──────────────────────────────────────────┬──────────────────┤
│ SelfPanel    │                                          │ IntentPanel      │
│──────────────│                                          │ (Agent)          │
│ Live view    │                                          │──────────────────│
│──────────────│               MapGrid                    │                  │
│ Paperdoll    │  (full height: terrain, walk memory,     │ OverseerPanel    │
│──────────────│   entities, trail)                       │ (chat timeline,  │
│ Lumber job   │                                          │ junctures, open  │
│ (lumber run) │                                          │ count, compose)  │
│              │                                          │                  │
├──────────────┴──────────────────────────────────────────┴──────────────────┤
│ ▸ details — Containers · Inspector · Gumps · Census · Diagnostics ·        │
│             Movement · Events                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Layout (reworked 2026-09-30, user request):** the map, the agent intent, the live view,
self/state and the overseer chat are the primary surface; everything else starts minimized.
ContainerTree and the Inspector/Gumps/Census/Diagnostics/Movement tabs plus the EventLog sit
in a collapsed bottom drawer (`<details>`, 300 px when open); the Movement tab stacks
MovementPanel and TrafficPanel (moved there from the left column 2026-10-06, user request).
Selecting an entity (map click, serial link) opens the drawer on the Inspector tab.

- **SelfPanel.** `world.self`: name, serial, vitals bars, stats, gold/weight, warmode,
  notoriety, buffs (`world.buffs[self]`), skills (top N; values ×10 fixed point, joined with
  `skill_names`). Buff names (here and in the EntityInspector, `BuffList`): the server's
  `title`, else its `cliloc` from the client's Cliloc.enu (`GET /api/cliloc?n=`; most Outlands
  buffs come with an empty title, e.g. icon 138 = cliloc 1044416 "Magic Reflection"), else
  `buff <icon>`.
- **MovementPanel (new, harness state).** From `movement`: true position and facing; the
  world-model position with a **DIVERGED** badge when they differ; inflight walks; ladder
  `next_seq`; `rejects_in_row` / **STALLED**; resync pending; client stale (re-anchor due). Below
  that, the last N proxy movement events (step, blocked, rejected, reanchor, token stamp/drop).
- **TrafficPanel (new).** From proxy-origin events: C2S packets by source (client vs agent) per
  packet id, confirms hidden and rewritten, fabricated client packets, and the last agent action.
  The one-glance answer to "what is the agent doing, and what did the proxy change".
- **MapGrid.** One `<canvas>`, centered on the true position, wheel zoom, drag pan.
  - **iso toggle** (2026-09-29): rotates the grid 45° clockwise into UO's own view: east goes
    down-right, south down-left, north up-right, and tiles are drawn as diamonds. UO's 44×44 tiles
    are squares turned 45°, so a pure rotation matches it (elevation isn't drawn). Labels stay upright.
    The choice persists in localStorage (`viz.mapProjection`). The projection math is in
    `viz/src/projection.ts` and is tested in `projection.test.ts`.
  - **Terrain underlay** (2026-09-29, `terrain` toggle, on by default, persisted as `viz.mapTerrain`)
    is the client's own `facet00.mul` from the install dir, read-only.
    - It's a 1 px/tile top-down colour picture (10752×6144), decoded by `harness/facet.py`.
    - `viz_server` serves it as 256-tile PNG chunks at `/api/facet/<cx>/<cy>.png`, with metadata at
      `/api/facet`. Flags: `--facet PATH` and `--no-facet`.
    - The browser fetches only the chunks in view and draws them unsmoothed, rotated in iso.
    - The 1 px/tile scale was verified on 40 bank markers (docs/NOTES.md).
    - It's colour only: no heights, no walkability. The file is dated 2024-12 and may miss recent map edits.
    - Tests: `harness/test_facet.py` (synthetic file) and `viz/src/facet.test.ts`.
  - **Underlay: walk memory** (`/api/walkmem`, plus live `step`/`blocked` events): known-walkable
    tiles shaded, confirmed edges faint, blocked moves as red ticks. It shows the planner's world directly.
  - **Off facet 0 the map is blank** (2026-10-08, `world.self.map != 0`, e.g. rental rooms on facet 3):
    the facet picture and the walk-memory/live-step layer are facet-0 data and are not drawn, so the
    room's coordinates don't show the unrelated facet-0 terrain at the same x/y. Self, mobiles, items and
    the trail still draw on the black background.
  - **Player houses** (2026-10-02): a ground item with `data_type` 2 is a multi. The map draws its
    footprint, not an item dot: walls on the ground storey solid tan, and foundation, steps and
    upper floors faint tan. The footprint comes from `GET /api/multi/<id>`, the house's pieces from the client's
    `multi.mul` (`viz_server.multi_footprint`, `uomap.multi_components`), and is fetched once per
    multi id (`viz/src/multis.ts`). Hover or click any footprint tile to pick the house. The
    footprint shows where the walk rules block (`pathfind.Walkers.get` places the same pieces).
    Verified in headless Chromium on replay 20261002_153718 paused at 17:38:49, the first of the
    12 denies at the Corpse Creek house: wall tiles drawn tan, and a click on a wall tile opened
    `0x431ACD5F` (graphic 0x0154, data_type 2) in the inspector.
  - Overlay: ground items (dots), **mobiles at their positions**, labeled with name plus title
    from type-6 `speech_heard` labels (e.g. "Len the banker"), notoriety colored; mobiles the
    client dropped out of view (`last_seen`, same facet, not dead) as hollow dimmed rings
    captioned "(last seen)"; self at the true position with a facing arrow, and a hollow ghost
    marker at the world-model position when diverged.
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
- **Theme (2026-10-02, user request: "a much more Ultima aesthetic").** All in `App.css`; no
  component changes except the map canvas chrome colours in `MapGrid.tsx`. Inspired by
  uooutlands.com (dark leather menu bar, gold bevelled title, parchment news scroll, crimson
  sidebar ribbons, Georgia text) and the client's gumps (bronze frames, paperdoll backdrop).
  - Panels are gump frames: grained dark-leather gradient, bronze border with a black hairline
    and a faint gold inner bevel (`--frame-shadow`); panel heads are brushed-bronze title bars
    with gold Cinzel `h2`. The header is a leather bar with a gold rule and an ankh before the title.
  - Overseer journal entries are ink on parchment (`.ov-overseer` redefines `--text`/`--dim`/`--link`
    locally); operator messages are royal-blue cloth; the details drawer bar is the crimson ribbon.
  - Fonts: Cinzel (display: headings, tabs, KPI values; latin subset from Google Fonts, OFL, in
    `viz/src/fonts/` with its licence file, inlined into `main.css` by `bun build` as a data URI) and
    Georgia (body, `--serif`: system font, nothing bundled; Palatino fallbacks). Body was Caudex until
    2026-10-08: its small x-height rendered jagged/tiny at 13px, so body is now Georgia 14.5px and every
    `font-size` in `App.css` was scaled x1.12 (rounded to 0.5px). Canvas text in `MapGrid.tsx` is unchanged.
  - Textures are inline SVG `feTurbulence` data URIs (`--grain`, `--mottle`), no image files.
  - Meaning-bearing colours stay as they were: ok/warn/bad/info badges (re-toned, same hues),
    notoriety, HP bands, the map's layer colours and legend, chart series. The lumber chart's
    rolling series moved from the old cyan accent to `--series` (pale blue), since the accent is
    now gold and would collide with the gold "boards stored" marks.

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
- Frontend: **Bun 1.4.2** (installed 2026-09-29 at `~\.bun\bin\bun.exe`, user
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
| `harness/viz_server.py` | `--live [--state-port 25942]` / `--replay TAG`; routes §2.1; SSE ring + resume; walk layer from the memory store (`--memory-db`); agent gate forwarding (§2.2); serves `viz/dist` |
| `harness/test_viz.py` | backend tests (§7) |
| `viz/package.json`, `bun.lock`, `tsconfig.json`, `index.html` | scaffold; scripts `build` (`bun build src/main.tsx --outdir dist`), `watch`, `test`, `typecheck` |
| `viz/src/types.ts` | TS types for §1 (StateResponse, Movement, Snapshot, Mobile, Item, Gump, EventEnvelope, WalkMemory) |
| `viz/src/api.ts`, `sse.ts`, `store.ts`, `serial.ts` | SSE client + resume, with per-task state supersession (§2.10); `useSyncExternalStore` store; int↔hex serial normalization and entity lookup |
| `viz/src/gate.ts`, `components/GateControls.tsx` | agent gate badge vocabulary and button rules; the header gate controls (§2.2) |
| `components/CaptchaToggle.tsx` | the header captcha mode toggle (§2.2a) |
| `viz/src/character.ts`, `components/CharacterPicker.tsx` | the chosen character (selector state, `withChar`, `pickChar`) and the header picker (§2.14) |
| `viz/src/intent.ts`, `components/IntentPanel.tsx` | agent intent view (tone, trip context, age against the live or replay clock) and the Avatar panel (§2.3) |
| `harness/jobs.py` | job analytics over the memory store (§2.4) |
| `viz/src/overseer.ts`, `components/OverseerPanel.tsx` | overseer timeline model (cursor merge, heartbeat status, chat validation) and the Overseer panel (§2.4) |
| `viz/src/jobs.ts`, `chart.ts`, `components/JobsPage.tsx`, `components/Charts.tsx` | Jobs page view model (KPIs, event wording, theft rule, wood shares), SVG chart geometry, the dashboard and its charts (§2.4) |
| `viz/src/lumberjob.ts`, `components/LumberJobPanel.tsx` | left-column Lumber job pane: run/leg detection, spot trips and wood mixes, and the panel (§2.11) |
| `viz/src/grove.ts` | the map's lumber grove layer: query choice, tree lookup and hover text (§2.12) |
| `harness/nystul.py`, `nystul_ext.ts`, `nystul_tools.py`, `nystul_memory.py`, `nystul_prompt.md`, `test_nystul.py` | Nystul the Wizard: conversations and omp runs, the omp extension, the read-only tool boundary, Codex proposals (validate/apply), the system prompt, tests (§2.13) |
| `viz/src/nystul.ts`, `markdown.ts`, `components/NystulChat.tsx`, `NystulPage.tsx`, `Markdown.tsx` | Nystul view model (validation, step labels, run summary), the markdown parser, the chat, page and renderer (§2.13) |
| `harness/uoart.py`, `harness/test_uoart.py` | `UooImages` (gumps.uoo / art.uoo reader, shared with `paperdoll.py`) and `ItemArt` for `/api/art` (§2.9) |
| `viz/src/App.tsx`, `components/*.tsx`, `App.css` | §4 panels |
| `viz/src/fonts/` | Cinzel woff2 (OFL; licence alongside), inlined into `main.css` by the build (§4 Theme) |

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
  - Coalescing: a burst yields one `state` message. A ping-pong burst the size of 20261005_093927's
    (624 pumps, ~15300 events): a stalled reader's mailbox stays ≤ 2000 and its next write is the
    newest state; a 4 MB/s reader over HTTP has the newest state < 1 s after the burst (0.24 s;
    11.5 s with the old FIFO), with every speech/intent/cliloc/gump envelope in order (§2.10).
  - Live poller against a proxy subprocess fed by the `test_errand.py`-style fake server; also
    asserts **zero C2S packets with `src` other than client/agent**, so the viz never injects.
  - Agent gate: `/api/gate` 409 in replay, 400 for `rearm`, 502 with the proxy down; live pause
    (the proxy's own `{"op":"gate"}` reports paused, and a control-port injection from the test
    gets `ERR agent paused`), resume, kill, and resume-while-killed 409. The proxy's control-port
    log shows only the test's own connection.
  - Characters (§2.14): `/api/sessions` 502 with the proxy down and lists the fake session once
    up; `/api/state?char=<serial>` matches the default feed, `?char=Nobody` gives `no session for
    character`; `/api/overseer?char=` scopes junctures, chat and heartbeat, a name is 400;
    `POST /api/chat {"char"}` stores `char_serial` and stays out of another character's view.
  - Agent intent: `spot` and `woods` kept, a tally-only update stays one step, malformed `woods`
    (negative, string, empty name, bool, list, 33 entries) and a 65-char `spot` dropped (§2.3).
  - `/api/lumber/grove` (§2.12): the area, every runner tree inside it, harvest-memory states
    (a fresh depleted mark with its window end, not_tree, an old unreachable ready again), trees
    past the edge only up to the margin, lookup by position (else `spot` null), 400s.
- **Proxy:** extend `test_movement.py` to check that proxy events and envelopes appear in the
  state-port event log.
- **Frontend (`bun test`), pure helpers only:** serial normalization, entity lookup precedence,
  SSE state supersession (only the newest state of a burst is applied, events keep their places),
  EventLog filters, ContainerTree builder, walk-memory layer builder, true-vs-dead-reckoned
  divergence rule, duration formatting, gate button rules per state, the store keeping a
  newer gate over an older state frame, and (§2.4) chart scales/ticks/bars, KPI tiles and tones,
  event wording, the theft-loss rule, wood shares, overseer cursor merging, timeline order,
  heartbeat status and chat validation, and the Jobs date range (query parsing and its inverse,
  local-midnight bounds on DST days, presets), and (§2.11) the lumber run/leg rules, the current
  logs/hr window, spot trips and wood mixes, and (§2.12) the grove query choice and tree hover
  text.
  `bunx tsc --noEmit` must pass. No DOM snapshot tests; M2–M4 acceptance runs
  are the integration check.
- **Jobs and overseer (`harness/test_viz.py`):** `jobs.analytics` numbers on a seeded store
  (totals, per-day split and UTC offset, rolling series, deaths by cause, thefts, value with and
  without woods, `since`, `until` exclusive at the boundary, a [since, until) window, a trip
  without `t_start` only in the unbounded range, determinism, empty data); `/api/jobs` equals
  `jobs.analytics`, also with `since`+`until`, and answers 400 to an empty, reversed, NaN or
  non-numeric range; `/api/jobs/plan` serves the plan (null with no store) and, while it computes,
  `/api/jobs` and `/api/overseer` answer at once;
  `/api/overseer` cursors, the newest-200 window, open ids after an ack, and heartbeat;
  `POST /api/chat` stores a trimmed `user` row and rejects empty, whitespace, missing,
  non-string and 2001-character text with 400; `/api/captcha` defaults to `human`, writes where
  the runner's `Memory` reads it, and rejects other modes with 400; a missing store answers empty
  without being created; live mode serves the same routes while the proxy is down.

---

## 9. As built (2026-09-29)

**Run**
- Build the frontend once: `cd viz && bun install && bun run build`. Bun lives at
  `~\.bun\bin\bun.exe`; `bun test` runs the tests and `bun run typecheck` runs
  `tsc --noEmit`.
- Live: `python harness/viz_server.py --live [--state-port 25942] [--port 8080]`, then open
  http://127.0.0.1:8080/. It only talks to the proxy's state port (polling, plus the agent-gate
  ops of §2.2), never opens the control port, and can be started or stopped at any time. A proxy started before this build shows world events only;
  restart the proxy for proxy events, traffic and diagnostics. Likewise a proxy started before
  2026-09-29 23:00 rejects the `intent` op (`unknown op`; runners log it and carry on), so the
  Avatar panel stays empty until the proxy is restarted.
- Replay: `python harness/viz_server.py --replay <TAG> [--rate 8] [--paused]` (TAG =
  `logs/session_<TAG>.*`).
- **Phone / home network (2026-10-03, user request).** Start with `--host 0.0.0.0`
  (`python harness/viz_server.py --live --host 0.0.0.0`), then open `http://protostar:8080/`
  (this machine's hostname; a phone may need `protostar.local`, depending on the router's DNS,
  which is unverified from here). The default stays `127.0.0.1`. Windows Firewall blocks inbound
  by default: run `allow_viz_lan.ps1` from an **Administrator** PowerShell once (TCP 8080, Private
  profile only, remote addresses limited to the local subnet; `-Remove` deletes the rule). It does
  not self-elevate, because the first version relaunched itself in a loop and flooded the desktop
  with PowerShell windows.
  - **Exposure:** the viz has no authentication. Anyone on the subnet can read the game state and
    the live view, and use the POST routes: agent gate pause/resume/kill, the captcha mode, and
    the overseer chat (text the overseer LLM reads). Fine on a trusted home LAN, not beyond it.
  - **Layout:** at ≤900px wide the page is one scrolling column (header, map, agent intent and
    overseer chat, side panels, details drawer); the desktop three-column grid is unchanged
    above that. `@media (pointer: coarse)` raises tap targets and uses 16px inputs (iOS zooms the
    page on smaller ones). On the map one finger pans, two pinch-zoom around their midpoint
    (`MapGrid.zoomAt`, same code as the wheel), a tap selects. Checked in headless Chromium at
    390×844 with touch emulation: no horizontal overflow on Live, Jobs (lumber, hunt); a CDP
    two-finger spread took the map from 16 to 59 px/tile. Not tried on a real phone.

**Files:** `harness/viz_feed.py` (Feed base: 2000-envelope ring, per-connection SSE mailboxes, ≤4 Hz pump;
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

- **Agent intent (§2.3), verified in headless Chromium** on the `test_loop_lumber.py` capture
  (replay, stepped). The panel showed "Going home: heading to the innkeeper" (`to_inn`,
  → 120,207, `lumber · trip 2/2`), "Waiting for you to solve the captcha in the client" (amber
  `captcha`) and "Chopping tree at 111,200" with the reticle on the tree next to the player.

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

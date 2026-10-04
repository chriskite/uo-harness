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
- Size: about 30 KB for a town scene (163420: 15 mobiles, 180 items). Full-snapshot pushes at
  ≤4 Hz are fine on localhost.

### 1.3 `events` = world-model events
Flat `{"ev": ..., ...}` dicts. **Serials are ints** (the snapshot uses hex strings; the frontend
normalizes). Vocabulary (`runtime.py` `_emit`):
- Session: `login`, `char_select`, `character_list`, `dialect_handshake` (flag1/flag2 = the
  S2C/C2S XOR keys), `keepalive`
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
- **Backend stack: stdlib only.** `http.server.ThreadingHTTPServer`, a poller/feeder thread, and a
  `queue.Queue` SSE fanout. HTTP threads serve a pre-serialized snapshot string that the
  poller/feeder thread swaps atomically (≤4 Hz, coalesced).

### 2.1 HTTP API (viz_server → browser)

| Route | Content |
|---|---|
| `GET /api/state` | the latest state-port response, verbatim (movement + world + recent events + diagnostics) |
| `GET /api/events` | SSE: `event: state` (full response, dirty-checked, ≤4 Hz) and `event: world_event` (each event envelope verbatim); `id:` = event seq; `Last-Event-ID` resume from a 2000-entry ring |
| `GET /api/walkmem` | walk memory (facet 0) projected from the harness memory store (docs/MEMORY.md), in the `nav.WalkMemory` JSON format (tiles, edges, blocked), cached 2 s |
| `GET /api/health` | viz_server mode (live/replay + session tag + order quality), poll lag, connection status, proxy diagnostics |
| `POST /api/playback` | replay only: `{play,pause,rate,step}` |
| `GET /api/gate` | live only: the proxy's agent gate (`{"op":"gate"}` on the state port), verbatim |
| `POST /api/gate` | live only: `{"action": "pause"\|"resume"\|"kill"}`, forwarded as `{"op":"gate","action":...}` (§2.2) |
| `GET /api/jobs?job=lumber[&since=T][&tz=M]` | job analytics from the memory store (`harness/jobs.py`, §2.4), cached 2 s |
| `GET /api/overseer?after_chat=N&after_juncture=M` | overseer chat rows and junctures above the cursors, open junctures, heartbeat (§2.4) |
| `POST /api/chat` | `{"text": T}` → a `user` chat row for the overseer (§2.4) |
| `GET /api/captcha`, `POST /api/captcha` | who answers the harvest captcha, `{"mode": "human"\|"auto"}` (§2.2a) |
| `GET /api/art/<graphic>.png` | an item's art from the client's `art.uoo`, cropped to its opaque pixels (§2.9); 404 JSON for an unknown/empty graphic or missing install data, 400 for a non-number |
| `GET /api/multi/<id>` | a house's footprint from the client's `multi.mul` (decimal or 0x hex multi id = a data_type 2 item's graphic): `{"id", "source", "tiles": [[dx, dy, "wall"\|"floor"]]}`, "wall" = an impassable piece below 20 z; 404 JSON for an unknown id (§4 MapGrid) |
| `GET /`, `/assets/*` | built frontend (`viz/dist/`) |

SSE rather than WebSocket: the flow is strictly server→browser, the browser `EventSource` gives
reconnect and resume for free, and it adds no dependencies. Full snapshots, not diffs: the store
is last-write-wins and small.

### 2.2 Agent gate controls

The proxy's agent gate (`harness/agent_gate.py`): a manual pause, a kill switch, forced jittered
breaks after ~2 h of agent-active time, and an 8 h/day agent-active cap. While it is closed every
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
  - `to_tree`, `chop` (text carries the log count), `captcha` (the previous intent is restored
    afterwards), `lockout`, `convert`
  - `to_bank` ("Going to the bank: …"), `open_bank`, `store` (since 2026-10-01; the room-era
    kinds were `to_inn`, `enter_room`, `to_box`, `store`, `exit_room`)
  - `trip_done`, `done`, `stopped` (abort or crash reason)
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
  - `store: false` when the store file does not exist (all empty).
- `POST /api/chat {"text": T}` → `Memory.chat_post("user", T.strip())` → `{"ok": true, "id": N}`.
  Text must be a string of 1..2000 characters after trimming; anything else is **400**, and bodies
  over 64 KiB are **413**. Besides this, the viz writes only the captcha mode (§2.2a).
- `GET /api/jobs?job=lumber|hunt[&since=T][&tz=M]` → `harness/jobs.py` `analytics()` plus `store`
  (`hunt` gets the hunt shape below, any other job the trip shape). `tz` is minutes east of UTC
  for the per-day split (the browser sends its own; default the server's local offset). Cached 2 s
  per (job, since, tz). Lumber also gets `plan`, computed at the server's clock (below).

**Analytics (`harness/jobs.py`, pure: `compute()` reads no clock).** Inputs: `Memory.episodes(job)`
(trip rows), `Memory.job_events(job, since)`, the `harvest_attempts` outcomes (lumber only; the
table has no job column) and `harness/data/woods.json` when present.
- **Per trip:** start/end, duration, logs, stored, logs/hr, captchas and wait, chop attempts and
  successes, phase times, the `woods: {name: n}` breakdown when the row has one, estimated value,
  and the job events that fell inside the trip.
- **Totals and per day** (a trip counts on the day its start falls on): trips, logs, stored,
  active hours, logs/hr, logs/trip, captchas, deaths by cause (`death` events, `data.cause` `pk` /
  `mob` / anything else → `other`), thefts (`theft` events: `data.amount` when numeric, else the
  summed `data.items`, which may be `{name: n}`, `[{name|graphic, amount}]` or `[name]`), PK
  sightings (`pk_seen`), flees (`flee`) and estimated value.
- **Active time** is the sum of trip durations, so idle time between runs doesn't dilute logs/hr.
- **Rolling series:** one point per trip end, with logs/hr over the trips that ended in the last
  hour of wall time (active time only) plus cumulative logs/hr.
- **Value:** `value_gp` per log of that wood from woods.json [INFERENCE: per unit; the economy
  research defines the unit]. A trip without a `woods` breakdown counts all its logs as
  `ordinary`, since the Shelter Island loop only chops ordinary trees. Logs of a wood with no known
  value are counted in `value_unpriced_logs` and never priced by guess. The value is null when no
  log could be priced, including when woods.json is absent.
- CLI: `python harness/jobs.py [--db PATH] [--job lumber|hunt] [--since T]` prints totals, days and
  harvest outcomes (hunt: totals, days and monsters).

**Lumber optimizer analytics (added 2026-10-03, user request: the dashboard shows what the
self-optimizing loop uses, docs/LUMBER_LOOP.md §6).**
- **Per trip** besides the above: `spot`, `outcome`, `why`, `place_fail`, `field_s` and
  `field_logs_per_hour` (field time as `lumber_opt.trip_obs` counts it: the λ sample),
  `time_split` {travel (recall legs and the walk to the library), lockout, field, other (walks,
  convert, bank)} summing to the duration, the `travel` legs, `skill`/`skill_end`, `supplies`,
  `players_seen`, `escapes`, `stationary_clears`, the hatchet and its last seen uses.
- `travel`: from the `travel` and `recall` job events, per leg kind (out/home/escape) the count,
  landed, casts, charge/spell, failures by reason, mean seconds and the mean walk to the library;
  per book (a library tome or ours) the charges it showed before each recall over time, the runes
  used. Rows from before 2026-10-03 have no book: an out leg's tome comes from the rune number in
  its row name and the Witcher table.
- `supplies` (summed trip `supplies`, priced from the store's `reagent:<name>` / `recall_charge`
  prices: `gp`, `unpriced`), `time_split` totals, `skill` points (trip start and end).
- `plan` = `jobs.lumber_plan`: `lumber_opt.plan_from_store` with no live character (skill from the
  newest trip row) and no position (every spot pays its travel prior), seeded by the minute, plus
  per spot the PK escapes (`recall`/`guard_flight` events), the last trip's outcome and why, and how
  it's reached (`Witcher rune N` or a walk). 0.18 s on the live store (2026-10-03: three hazards and a
  finer trip-size search), so it's computed per request (inside the 2 s cache).
  `analytics(plan_now=None)` (tests, the CLI) leaves it null.

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
- **Jobs page:** one dashboard per job, switched in its head (`Lumber` / `Hunting`; the hash is
  `#jobs` or `#jobs/hunt`). Both refresh every 15 s or with ↻. The lumber dashboard:
  - KPI tiles: logs/hr, logs/trip (with the chop success rate), trips, active hours, deaths to PKs
    (with PK sightings), deaths to mobs, loss to thieves, captchas (with the wait time). Safety
    tiles are green at 0, red or amber otherwise.
  - A line under the tiles: the estimated value (and how many logs are unpriced) and the chop
    outcomes from `harvest_attempts`.
  - **Optimizer: next pick** (2026-10-03; hazards 2026-10-03): the plan's spot with
    `explore`/`exploit`, P(best), trips × Q* logs, expected trip minutes, expected logs banked per
    trip with P(death) and P(sent home) per trip, and net logs/hr; then skill and chop success,
    the regrowth window (fitted or default), P(death | PK seen), the pooled creature-death and
    theft rates (thefts seen, share of the load each), the gear at risk on death (items on hover;
    "none (Young)"), the room left for logs when known, the new-spot prior, the dispersion and the
    `ctl run lumber` command.
  - **Spots**: every active spot and every spot with trips (the untried candidates are counted in
    the head): status, how it's reached, trips, field hours, field logs/hr with its 80% interval,
    net logs/hr, Q*, overhead, travel minutes, PK sightings/hr, deaths/hr (h_D; count on hover),
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
    - logs per trip as bars, with the stored-boards mark, captcha dots, death marks and the mean;
    - a trips-and-events strip, with the event list below it (travel events are in the travel
      panel instead).
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
  buy recall scrolls': 2 result(s)" or "briefed: 1 relevant, 2 standing". Below it are the
  ranked entries, each with #id, a kind badge, the topic, the content, score and confidence.
  A brief is split into "relevant here" and "standing rules".
- **codex** (amber): remembered, confirmed, updated or retracted. For an add, the content is
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
│ ▸ Movement & │                                          │ junctures, open  │
│   traffic    │                                          │ count, compose)  │
├──────────────┴──────────────────────────────────────────┴──────────────────┤
│ ▸ details — Containers · Inspector · Gumps · Census · Diagnostics · Events │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Layout (reworked 2026-09-30, user request):** the map, the agent intent, the live view,
self/state and the overseer chat are the primary surface; everything else starts minimized.
MovementPanel and TrafficPanel fold into a collapsed `<details>` at the foot of the left
column; ContainerTree and the Inspector/Gumps/Census/Diagnostics tabs plus the EventLog sit in
a collapsed bottom drawer (`<details>`, 300 px when open). Selecting an entity (map click,
serial link) opens the drawer on the Inspector tab.

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
  sidebar ribbons, Caudex text) and the client's gumps (bronze frames, paperdoll backdrop).
  - Panels are gump frames: grained dark-leather gradient, bronze border with a black hairline
    and a faint gold inner bevel (`--frame-shadow`); panel heads are brushed-bronze title bars
    with gold Cinzel `h2`. The header is a leather bar with a gold rule and an ankh before the title.
  - Overseer journal entries are ink on parchment (`.ov-overseer` redefines `--text`/`--dim`/`--link`
    locally); operator messages are royal-blue cloth; the details drawer bar is the crimson ribbon.
  - Fonts: Cinzel (display: headings, tabs, KPI values) and Caudex (body), latin subsets from Google
    Fonts, OFL, in `viz/src/fonts/` with their licence files. `bun build` inlines them into
    `main.css` as data URIs, so the viz needs no network and `viz_server` serves nothing new.
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
| `harness/viz_server.py` | `--live [--state-port 25942]` / `--replay TAG`; routes §2.1; SSE ring + resume; walk layer from the memory store (`--memory-db`); agent gate forwarding (§2.2); serves `viz/dist` |
| `harness/test_viz.py` | backend tests (§7) |
| `viz/package.json`, `bun.lock`, `tsconfig.json`, `index.html` | scaffold; scripts `build` (`bun build src/main.tsx --outdir dist`), `watch`, `test`, `typecheck` |
| `viz/src/types.ts` | TS types for §1 (StateResponse, Movement, Snapshot, Mobile, Item, Gump, EventEnvelope, WalkMemory) |
| `viz/src/api.ts`, `store.ts`, `serial.ts` | SSE client + resume; `useSyncExternalStore` store; int↔hex serial normalization and entity lookup |
| `viz/src/gate.ts`, `components/GateControls.tsx` | agent gate badge vocabulary and button rules; the header gate controls (§2.2) |
| `components/CaptchaToggle.tsx` | the header captcha mode toggle (§2.2a) |
| `viz/src/intent.ts`, `components/IntentPanel.tsx` | agent intent view (tone, trip context, age against the live or replay clock) and the Avatar panel (§2.3) |
| `harness/jobs.py` | job analytics over the memory store (§2.4) |
| `viz/src/overseer.ts`, `components/OverseerPanel.tsx` | overseer timeline model (cursor merge, heartbeat status, chat validation) and the Overseer panel (§2.4) |
| `viz/src/jobs.ts`, `chart.ts`, `components/JobsPage.tsx`, `components/Charts.tsx` | Jobs page view model (KPIs, event wording, theft rule, wood shares), SVG chart geometry, the dashboard and its charts (§2.4) |
| `harness/uoart.py`, `harness/test_uoart.py` | `UooImages` (gumps.uoo / art.uoo reader, shared with `paperdoll.py`) and `ItemArt` for `/api/art` (§2.9) |
| `viz/src/App.tsx`, `components/*.tsx`, `App.css` | §4 panels |
| `viz/src/fonts/` | Cinzel + Caudex woff2 (OFL; licences alongside), inlined into `main.css` by the build (§4 Theme) |

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
  divergence rule, duration formatting, gate button rules per state, the store keeping a
  newer gate over an older state frame, and (§2.4) chart scales/ticks/bars, KPI tiles and tones,
  event wording, the theft-loss rule, wood shares, overseer cursor merging, timeline order,
  heartbeat status and chat validation.
  `bunx tsc --noEmit` must pass. No DOM snapshot tests; M2–M4 acceptance runs
  are the integration check.
- **Jobs and overseer (`harness/test_viz.py`):** `jobs.analytics` numbers on a seeded store
  (totals, per-day split and UTC offset, rolling series, deaths by cause, thefts, value with and
  without woods, `since`, determinism, empty data); `/api/jobs` equals `jobs.analytics`;
  `/api/overseer` cursors, the newest-200 window, open ids after an ack, and heartbeat;
  `POST /api/chat` stores a trimmed `user` row and rejects empty, whitespace, missing,
  non-string and 2001-character text with 400; `/api/captcha` defaults to `human`, writes where
  the runner's `Memory` reads it, and rejects other modes with 400; a missing store answers empty
  without being created; live mode serves the same routes while the proxy is down.

---

## 9. As built (2026-09-29)

**Run**
- Build the frontend once: `cd viz && bun install && bun run build`. Bun lives at
  `C:\Users\chris\.bun\bin\bun.exe`; `bun test` runs the tests and `bun run typecheck` runs
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

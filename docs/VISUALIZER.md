# VISUALIZER.md — human-consumable world-state visualizer (design)

Date: 2026-09-28. Status: **design only — nothing here is implemented**.

Goal: a small web app that shows a human exactly what the harness knows about the
world — the same `StateStore.snapshot()` + `drain_events()` stream the LLM agent
(PLAN.md Phase 4) will consume — against either a **captured session replay** (no
game needed) or the **live proxy tap** (INTERCEPTION.md chain up).

Everything below is grounded in the actual runtime code: `harness/world/state.py`
(store shape), `harness/world/runtime.py` (event vocabulary), `harness/replay.py`
(offline decode), `harness/proxy.py` (live tap).

---

## 1. What the data actually looks like

The visualizer has exactly two inputs, both from `WorldRuntime`:

```python
rt = WorldRuntime()
rt.feed_packet(direction, payload)   # "c2s" | "s2c", framed packet bytes
snap = rt.state.snapshot()           # JSON-serializable dict
events = rt.drain_events()           # list[dict], destructive read
```

### 1.1 `snapshot()` shape (state.py `StateStore.snapshot()`)

Serial keys are rendered as `0x`-prefixed uppercase hex strings; dataclass fields
with `None` values are omitted from mobiles/items (`to_dict` filters them):

```json
{
  "self": {
    "serial": "0x00094375", "name": "TestWorth", "account": "...",
    "x": 1531, "y": 902, "z": 0, "direction": 4,
    "position_absolute": true, "position_changes": 87,
    "hits": 100, "hits_max": 100, "mana": 40, "mana_max": 40,
    "stam": 90, "stam_max": 90, "gold": 12450, "weight": 212,
    "warmode": false, "notoriety": 1,
    "stats": {"str": 60, "dex": 55, ...},
    "skills": {"12": {"value": 610, "base": 610, "lock": 0, "cap": 1000}},
    "skill_names": ["...", "..."]
  },
  "protocol_version": 12,
  "mobiles": {
    "0x000001D3": {"name": "...", "graphic": 400, "hue": 0,
                   "hits": 50, "hits_max": 50, "notoriety": 3,
                   "poisoned": false, "flags": 0}
  },
  "items": {
    "0x40000D54": {"name": "...", "graphic": 5366, "amount": 1,
                   "x": 1533, "y": 905, "z": 0, "hue": 0, "flags": 0,
                   "container": "0x00094375", "layer": 21, "grid": 5}
  },
  "gumps": [
    {"serial": "0x000001D3", "gump_id": "0x00000030", "x": 20, "y": 20,
     "layout": "{ ... }", "lines": ["..."], "open": true,
     "compressed": false, "responses": 0}
  ],
  "target": {"active": false, "target_type": null,
             "cursor_id": null, "cursor_type": null},
  "census": {"0x000001D3": {"queries": 3, "sources": ["0x09", "0xff09"]}},
  "names": {"0x000001D3": "..."},
  "buffs": {"0x00094375": {"1040": {"title": "...", ...}}},
  "containers": ["0x400012AB"]
}
```

Semantics that matter for rendering (all from state.py / runtime.py):

- **Self position is dead-reckoned** from C2S walk packets (`_h_walk` applies the
  8-direction deltas) and starts **relative at (0, 0)**; it only becomes world
  coordinates when 0x20 UpdatePlayer / 0x21 DenyWalk arrives
  (`position_absolute: false → true`). The UI must badge this state, not silently
  plot relative coordinates as if they were absolute.
- **Items carry coordinates only when on the ground** (`container: null`); items in
  containers/equipment carry `container` (parent serial, hex), `layer`, `grid`.
- **Mobiles currently have NO coordinates.** The `Mobile` dataclass (state.py
  lines 66–83) has serial/name/graphic/hue/vitals/notoriety/poisoned/flags and
  nothing else — the tracked packet set has no mobile-position source (0x20 is
  player-only; 0x77/0x78 are not yet parsed — see §5.1). MapGrid Phase A
  therefore plots **self + ground items** and shows mobiles in a roster; true
  mobile plotting is an explicit prerequisite task, not a visualization problem.
- `gumps` is keyed `(serial, gump_id)` internally; the layout string is the raw
  UO gump command block (`{ ... }`), lines are the text-line table.
- `census` = serials **the client itself** queried (0x09/0x34/0x98 + dialect
  sub 9) — a salience signal, per `EntityCensus` (state.py lines 144–161).

### 1.2 `drain_events()` vocabulary (runtime.py `_emit` sites — exhaustive)

Events are flat dicts `{"ev": <name>, ...fields}`. **Serials in events are raw
ints, not hex strings** (unlike the snapshot) — the frontend must normalize when
joining events to entities.

| Event | Fields (beyond `ev`) | Source |
|---|---|---|
| `login` | account | C2S 0x91 |
| `char_select` | name | C2S 0x5D |
| `dialect_handshake` | version, flag1, flag2 | S2C 0xFF sub 0 (the 19-byte prelude) |
| `keepalive` | direction[, timestamp] | 0xFF sub 3, both directions |
| `walk` | dir, run, seq, x, y, moved | C2S 0x02 (dead-reckon) |
| `walk_confirm` | seq, notoriety | S2C 0x22 |
| `walk_deny` | seq, x, y, z, direction | S2C 0x21 (absolute position correction) |
| `query` | serial, kind | C2S 0x09/0x34/0x98 (entity salience) |
| `item_query` | serial | C2S 0xFF sub 9 (dialect detail query) |
| `names` | count, entries[{serial, name}] | S2C 0xFF sub 0x15 |
| `speech` | type, hue, font, lang, text | C2S 0xAD |
| `dclick` | serial | C2S 0x06 |
| `damage` | serial, amount | S2C 0x0B |
| `swing` | attacker, defender | S2C 0x2F |
| `spell_cast` | spell_id, flag | C2S 0xFF sub 4 |
| `animation` | serial, action, frames, repeat, backward, repeat_flag, delay | S2C 0x6E |
| `item_seen` | serial, graphic, container | S2C 0x1A/0xF3/0x2E/0x25 |
| `delete` | serial | S2C 0x1D |
| `container_open` | serial, gump_id | S2C 0x24 |
| `container_content` | count | S2C 0x3C |
| `gump_open` | serial, gump_id, x, y, layout, lines | S2C 0xB0/0xDD |
| `gump_response` | serial, gump_id, button_id, switches, texts | C2S 0xB1 |
| `target` | target_type, cursor_id, cursor_type | S2C 0x6C |
| `target_response` | serial, cursor_id, target_type, x, y, z, graphic | C2S 0x6C |
| `buff_update` | serial, icon_id, title | S2C 0xFF sub 8 |
| `buff_remove` | serial, buff_id | S2C 0xFF sub 9 |

Silent mutations (no event): warmode flips (0x72), stat updates (0xA1/A2/A3),
skills (0x3A), self position on 0x20, mobile vitals (0x2D), healthbar/poison
flags (0x16/0x17). **Consequence: the event stream alone cannot reconstruct
state — the UI must consume both channels** (see §2.3).

`drain_events()` is destructive and has **no timestamps**; the viz server adds
`seq`/`t` envelope fields at drain time (§2.3). Runtime diagnostics
(`packet_counts`, `unhandled`, `dialect_unhandled`, `parse_failures`,
`anomalies`) live on the runtime, not in the snapshot.

---

## 2. Architecture

### 2.1 Processes

```
 REPLAY MODE (no game):
   logs/session_*.{c2s,s2c}.raw ──decode/frame──> [viz_server.py: WorldRuntime]
                                                        │ snapshot() / drain_events()
                                                        ▼
                                              HTTP :8080  ──>  React app (viz/)

 LIVE MODE (game attached):
   ClassicUO.exe ──WinDivert NAT──> [viz_server.py: proxy relay + SessionTap hook
                                     + WorldRuntime] ──> 74.91.115.123:2593
                                                        │
                                                        ▼
                                              HTTP :8080  ──>  React app (viz/)
```

One Python process: **`harness/viz_server.py`** embeds a `WorldRuntime` and serves
the API over HTTP. In replay mode it decodes a capture and feeds packets on a
paced schedule. In live mode it *is* the proxy: it reuses `proxy.py`'s relay and
`SessionTap`, adding one callback so every framed packet is also fed to the
runtime (the only change to existing code — see §6).

Splitting the proxy and the viz server into two processes (and tailing the
`.jsonl` log) was considered and **rejected**: the jsonl side-log truncates
packet hex at 64 bytes, and re-framing from it would silently corrupt large
packets (e.g. 0xDD gumps, 0x3A skill lists). The live tap must see full framed
packets, which only the in-process `SessionTap` has.

### 2.2 Backend stack — stdlib only

- **`http.server.ThreadingHTTPServer`** for HTTP. Justification: zero new
  dependencies; the API surface is 4 GET routes; the workload is one human
  browser. No aiohttp/FastAPI/uvicorn.
- **asyncio loop in a thread** for the live proxy relay (proxy.py is asyncio);
  packet feeds cross to the HTTP world through the dirty-flag/snapshot-cache
  machinery below. Replay mode needs no asyncio at all (a plain feeder thread).
- **`queue.Queue` fanout** for SSE subscribers.

Thread-safety rule: `StateStore` is only ever mutated on the feeder thread
(asyncio thread in live mode, feeder thread in replay). HTTP threads never call
`snapshot()` directly; they serve a **cached, pre-serialized snapshot string**
that the feeder thread regenerates (at most every 250 ms, coalescing bursts)
whenever it has fed at least one packet. Publishing is a single atomic reference
assignment, so readers never iterate a mutating dict.

### 2.3 Wire API

| Route | Content | Purpose |
|---|---|---|
| `GET /api/snapshot` | `application/json` — verbatim `StateStore.snapshot()` | initial load; manual refresh; debugging |
| `GET /api/events` | `text/event-stream` (SSE) | push channel: state ticks + events (below) |
| `GET /api/health` | JSON: mode, packets fed, `parse_failures`, `unhandled` top-10, uptime | harness diagnostics — **not** world state |
| `GET /`, `GET /assets/*` | static | built frontend (production); in dev, Vite serves and proxies `/api` |

**SSE chosen over WebSocket.** The data flow is strictly server→client (the
visualizer is a read-only observer; agent control is out of scope). SSE gives us:
plain HTTP (works through any dev proxy), automatic reconnect + `Last-Event-ID`
resume built into the browser `EventSource`, and zero dependencies (WebSocket
would require `websockets` and a handshake/framing layer for a capability we
don't use). If a future phase adds human→agent commands, add a plain
`POST /api/command` then — still no WebSocket.

**One SSE stream, two message types:**

```
id: 1042
event: state
data: {"self": {...}, "mobiles": {...}, ...}        ← full snapshot, dirty-checked, ≤4 Hz

id: 1043
event: world_event
data: {"seq": 1043, "t": 1759071234.567, "data": {"ev": "walk", "dir": 4, "run": false, "seq": 91, "x": 1531, "y": 903, "moved": true}}
```

- `state` messages carry the **full snapshot**, not diffs. This mirrors the
  store's own last-write-wins semantics, keeps the frontend a dumb replace
  function, and at this scale (a town scene ≈ tens of entities ≈ tens of KB)
  diffing machinery would be unjustified complexity.
- `world_event` wraps each `drain_events()` entry **unmodified** in
  `{"seq", "t", "data"}` — the server timestamps and sequences but never
  transforms the payload (parity principle, §3).
- `id:` = the same monotonically increasing `seq`. The server keeps a
  `collections.deque(maxlen=1000)` ring of recent messages; a reconnecting client
  sends `Last-Event-ID` and gets the missed suffix replayed, then a fresh
  `state` message (state after a gap is always a full snapshot — no delta math
  anywhere).

The frontend keeps exactly one store: `{snapshot, events[]}` — `state` replaces
`snapshot`, `world_event` appends to a bounded ring (default 500 shown).

### 2.4 Frontend stack — `viz/`

React + Vite + TypeScript. Full dependency list, each justified:

| Dependency | Why it's in | Alternative rejected |
|---|---|---|
| `react`, `react-dom` | UI: 7 live panels with independent update rates; hand-rolled DOM diffing is the real alternative and is strictly worse | — |
| `vite`, `@vitejs/plugin-react` | dev server with `/api` proxy + one-command build | — |
| `typescript` | snapshot/event shapes are fixed contracts (§1); type them once in `src/types.ts` and every component is checked against the real API | — |

Explicitly **not** included: Redux/Zustand/MobX (one store, `useSyncExternalStore`
is enough), react-router (single page), a canvas/scene library (Phase A is dots
and labels on one `<canvas>`; reassess only at Phase C), CSS frameworks (plain
CSS grid), test-renderer libraries beyond vitest (§7).

---

## 3. Observability parity principle

**The visualizer consumes exactly `snapshot()` + `drain_events()` — the same two
calls the LLM agent will make. There is no privileged channel.**

Concretely:

- The viz server never reaches into `StateStore` internals, parser internals, or
  the proxy log to render the world view. If the human can't see it, the agent
  can't see it either — and vice versa.
- Server-side enrichment is limited to transport metadata (`seq`, `t`) that the
  agent client would add itself; event payloads are forwarded byte-identical
  (including the int-vs-hex serial inconsistency — the frontend normalizes for
  display, documenting the wart rather than hiding it).
- `/api/health` exposes runtime *diagnostics* (unhandled ids, parse failures) —
  meta-information about the harness, not world state. It is labeled as such and
  is the only exception.

This makes the visualizer the **ideal agent-development debugging surface**:

1. **Replay determinism as a debugger.** `test_world_replay.py` proves replay is
   deterministic (`e1 == e2` on both captures). Run the agent and the visualizer
   side by side against the same capture: every agent decision can be audited
   against what was actually observable at that moment — scroll the EventLog to
   the triggering event, inspect the exact snapshot the planner saw.
2. **Coverage-gap discovery.** Unhandled packet ids and dialect subs surface in
   `/api/health`; a human watching the live view notices "the vendor window
   opened in-game but nothing changed here" long before an agent misbehaves from
   the blind spot.
3. **Client-library sharing.** The TypeScript `api.ts` (SSE + snapshot types) is
   the reference client for the *shape* of the data; the Python agent client
   mirrors it. One contract, two consumers, no drift.
4. **Ground-truth capture for evals.** "Replay session X, screenshot the viz at
   event N" is a reproducible fixture for agent regressions.

---

## 4. Frontend components

Single page, CSS grid. All panels read from the one store; EntityInspector is
driven by a `selectedSerial` in local App state (set by MapGrid clicks,
ContainerTree clicks, EventLog clicks).

```
┌──────────────────────────────────────────────────────────────────────────┐
│ header: mode badge (REPLAY session_20260928_164548 | LIVE :2593)         │
│         conn status • packets fed • position: ABSOLUTE/RELATIVE • ⏯ ⏩   │
├───────────────┬──────────────────────────────────────┬───────────────────┤
│ SelfPanel     │                                      │ EventLog          │
│               │            MapGrid                   │  (filterable,     │
│───────────────│        (player-centered)             │   live)           │
│ CensusPanel   │                                      │                   │
├───────────────┴──────────────────────┬───────────────┴───────────────────┤
│ ContainerTree                        │ EntityInspector / GumpViewer      │
└──────────────────────────────────────┴───────────────────────────────────┘
```

### 4.1 SelfPanel

```
┌─ TestWorth (0x00094375) ──────────┐
│ ⚔ WAR   noto 1   pos ABS (1531,902,0) ↓S
│ HP  ██████████░░  82/100           │
│ MANA ████████░░░░  40/52           │
│ STAM ████████████  90/90           │
│ STR 60  DEX 55  INT 42             │
│ gold 12,450   weight 212/…         │
│ buffs: 1040 "…", 1051 "…"          │
│ skills ▾ (top 5 by value)          │
│  Magery      61.0 / 61.0  🔒→      │
│  …                                 │
└────────────────────────────────────┘
```

Reads `snapshot.self` (+ `snapshot.buffs[self.serial]`). Shows
`position_absolute` as a badge — when RELATIVE, MapGrid shows a matching
warning. Warmode from `self.warmode`. Skills join `skills` (keyed by str id,
×10 fixed-point values) with `skill_names`.

### 4.2 MapGrid

Top-down plot, one `<canvas>`, player-centered, wheel-zoom, drag-pan. Phase A is
schematic (dots + labels); Phases B/C change the *underlay*, not the overlay.

```
┌─ MapGrid ──────────────────────────── [rel pos!] [24px/tile]─┐
│                                                              │
│        · dagger(0x40001…)         ▓ wall(static, PhB)        │
│                    🧍 a vendor(?) — roster link, no pos yet  │
│      · gold pile ×84                                          │
│                ▶ YOU (TestWorth)   ← center, dir arrow       │
│   · lantern              · crate                             │
│                                                              │
│  click dot → EntityInspector    hover → serial+graphic       │
└──────────────────────────────────────────────────────────────┘
```

- Render set: `self` (center marker + direction arrow from `direction` 0–7,
  using the same delta convention as runtime.py `_DELTAS`) and every **ground
  item** (`items` where `container` is absent and `x`/`y` present).
- **Mobiles are NOT plotted in Phase A** — `Mobile` has no coordinates (§1.1).
  They appear in a roster strip at the panel edge (clickable → inspector), and
  the panel header counts them, so the gap is visible rather than silent.
  Plotting them is milestone M6 (runtime extension, §5.1).
- Hue → dot color via a fixed fallback table (notoriety/hue buckets); real hue
  rendering is Phase C.
- Coordinate space: if `position_absolute` is false, plot in the dead-reckoned
  relative frame and badge it; the transition to absolute on 0x20/0x21 just
  recenters (items only arrive with absolute coords, so the relative frame
  typically contains only the player — fine).

### 4.3 EntityInspector

```
┌─ 0x000001D3 ────────────────────────┐
│ type MOBILE   name "a vendor"       │
│ graphic 0x0190  hue 0  noto 3       │
│ HP 50/50  poisoned no  flags 0x00   │
│ census: 3 queries via [0x09,0xff09] │
│ buffs: —                            │
│ (items: amount, x/y/z, layer, grid, │
│  container → jump-link)             │
└─────────────────────────────────────┘
```

Lookup order: `mobiles[serial]` → `items[serial]` → `names[serial]`-only
(serial seen via census/names but never upserted — render "known by name/query
only"). Joins census + buffs for the serial. Container field links into
ContainerTree selection.

### 4.4 ContainerTree

```
┌─ Containers ────────────────────────┐
│ 🌍 ground (12 items)                │
│ 🎒 0x00094375 TestWorth [OPEN]      │
│    ├ equipment (layer)              │
│    │   ├ L21: robe 0x1F03           │
│    │   └ L06: kryss 0x1401          │
│    └ backpack grid                  │
│        └ reagent bag 0x0E79 ▸       │
│ 🧰 0x400012AB [OPEN] (vendor shop)  │
└─────────────────────────────────────┘
```

Built client-side from three snapshot fields: `items[*].container` (parent
links), `items[*].layer` (equipment slots), `containers` (serial set marked
OPEN). Roots: ground items (`container` absent) and the self serial. Vendor
shop gumps (`gump_id 0x30`, WORLDMODEL.md §4) annotate their container node.

### 4.5 GumpViewer

```
┌─ Gumps ─────────────────────────────┐
│ [0x000001D3 / 0x30 vendor] [0x0/…]  │  ← tab per open gump
│ serial 0x000001D3 gump 0x00000030   │
│ @ (20,20)  compressed no  resp 1    │
│ ┌ layout (raw) ───────────────────┐ │
│ │ { page 0 } { resizepic … } …    │ │
│ └─────────────────────────────────┘ │
│ lines: 0 "Buy"  1 "Sell" …          │
└─────────────────────────────────────┘
```

Phase A renders the **raw layout command string + numbered text lines** — that
is literally what the agent sees, and it's enough to debug gump-response
mismatches. A parsed/schematic rendering of `{button …}`/`{text …}` commands is
a Phase C nicety (the layout grammar is upstream-stable), explicitly deferred.

### 4.6 EventLog

```
┌─ EventLog ── [filter: walk▾] [✓hide keepalive] ───┐
│ 1043 14:12:31.2  walk        dir=4 seq=91 →(1531,903)
│ 1044 14:12:31.3  walk_confirm seq=91 noto=1       │
│ 1047 14:12:32.0  speech      "buy"                │
│ 1048 14:12:32.1  query       0x40000D54 kind=0x34 │
│ 1052 14:12:32.4  names       2 entries            │
└───────────────────────────────────────────────────┘
```

Live-appending list from `world_event` SSE messages. Filter dropdown populated
from the observed `ev` vocabulary; keepalive hidden by default (sub-3 fires
~1/s, WORLDMODEL.md §5). Clicking an event whose payload has a `serial` selects
it in EntityInspector; serials render hex (normalized from int, §1.2).
Pause-scroll button; the display ring is bounded (500), the server ring is
separate (1000, for resume).

### 4.7 CensusPanel

```
┌─ Census (client salience) ──────────┐
│ serial        queries  sources      │
│ 0x000001D3      3      09 ff09      │
│ 0x40000D54      2      34 ff09      │
│ …sorted by queries desc…            │
└─────────────────────────────────────┘
```

Straight render of `snapshot.census` — what the *client* chose to look at
(state.py `EntityCensus`). Doubles as a "what does the player find interesting"
signal and as ground truth when auditing query → name-response pairing
(`names` events answer `query`/`item_query`).

---

## 5. Fidelity roadmap

### Phase A — schematic map (this document's build plan, M1–M5)

Colored dots + labels per entity class on a blank canvas. Everything in §4.
Effort: **~2 days** total across milestones. Unlocks: the full debugging
surface; agent-development parity; replay scrubbing.

### Phase B — real map underlay

Render actual land tiles + statics around the player so dots sit on real
terrain.

- **Source files**: the Outlands fork loads its map through its own `UOO.*`
  file layer — `UOO.TerrainFile` is the map store read by
  `ClassicUO.Game.Map.Chunk::Load` (WORLDSTATE.md §2). The on-disk files live in
  the client install dir (`C:\Program Files (x86)\Ultima Online Outlands`) —
  read-only access only (README: never write into it).
- **Format status — honest assessment**: the repo currently contains **no
  characterization of the map file format** (no header notes exist in
  docs/NOTES.md as of this writing; the fork's `.uoo` family is unnamed in our
  docs so far). Upstream ClassicUO's `map0.mul`/`staidx0.mul`/`statics0.mul`
  formats are public knowledge and the fork's files are plausibly close or a
  container variant — but that is **[INFERENCE]** until inspected.
- **Plan**: (1) hexdump + entropy-scan the candidate files and compare against
  the known MUL layouts; (2) fallback (per the project brief): Ghidra-decompile
  the loader — `UOO.TerrainFile` readers and `Chunk::Load` — using the
  established headless workflow (NOTES.md §Ghidra) and the name→RVA map
  (`mrt_map.json`) to jump straight to the parse functions; (3) write
  `harness/uo/mapfile.py` + unit tests against the real files; (4) backend
  route `GET /api/map?x=&y=&r=` returning a tile-rect bitmap/PNG (pre-rendered
  land colors via radarcol if decodable, else a palette hash of land tile ids —
  statics drawn as dark dots); MapGrid draws it as the underlay.
- **Statics vs items**: statics come from the map files (client-side world);
  ground items come from the snapshot (server-sent). Render them in different
  visual weights so the human can tell "world geometry" from "tracked entity".
- Effort: **1–3 days** (the format unknown is the whole risk; renderer is ~½
  day once bytes are understood). Unlocks Phase C placement context and makes
  warps/`[Go` transitions visually obvious.

### Phase C — sprite art + true hues

- Decode the art tile data (upstream equivalent: `art.mul`/`tiledata.mul`) for
  item/mobile sprites; apply `hues.mul` partial hues; use `radarcol.mul` for
  the map underlay colors. Same discovery procedure as Phase B (the fork's
  `UOO.*` loaders), same fallback (Ghidra on the art loader).
- Mobile paperdoll/walk animation is **explicitly out of scope**; single-frame
  sprite per graphic is the target.
- Effort: **2–4 days**. Unlocks: a view that reads like the real client —
  useful for demoing and for fast visual sanity checks against the game window.

**What unlocks what**: A → (agent dev starts). B requires only the client
install files, not more captures. C requires B's file-access machinery and adds
nothing structural. B and C are pure renderer upgrades: the snapshot contract
and every component above them stay unchanged.

### 5.1 Cross-cutting prerequisite: mobile positions (M6)

Plotting mobiles on MapGrid requires the runtime to learn mobile coordinates —
a `StateStore`/parser change, not a viz change:

- Add parsers/handlers for the mobile draw/move packets (upstream 0x77
  MobileMoving / 0x78 MobileIncoming; 0x77 observed live in session 164548 at
  18 bytes, matching the Outlands-extended length — WORLDMODEL.md §6 note).
- Extend `Mobile` with `x/y/z/dir` and upsert from those handlers.
- The visualizer picks this up for free (mobiles gain coordinates → dots
  appear). Acceptance: live session, another mobile walks across the screen,
  its dot tracks within the tick cadence.

---

## 6. Build plan (file-by-file, ordered milestones)

### New/changed files

| File | Contents |
|---|---|
| `harness/viz_server.py` | Modes (`--replay TAG` / `--live`), HTTP server (`/api/snapshot`, `/api/events` SSE + `Last-Event-ID` resume ring, `/api/health`, static), snapshot-cache + dirty-tick, event envelope/seq, replay pacing controller (play/pause/speed/step via `POST /api/playback`) |
| `harness/viz_feed.py` | Shared packet-source iterators: `iter_replay(c2s, s2c)` yields `(direction, payload, t_or_None)` incrementally (framing/decode logic factored out of `replay.py` — prelude, XOR, Huffman, drop-byte resync, unchanged semantics); `iter_live` wraps a `SessionTap` subclass whose hook feeds the runtime |
| `harness/proxy.py` | **One small change**: `SessionTap` gains an optional `on_packet(direction, payload)` callback invoked where packets are currently logged (`_drain_c2s`/`_drain_s2c`); default `None` = current behavior |
| `harness/test_viz.py` | Backend tests (§7), registered into `test_world.py` |
| `viz/package.json`, `vite.config.ts`, `tsconfig.json`, `index.html` | Scaffold; vite dev proxy `/api → 127.0.0.1:8080` |
| `viz/src/types.ts` | TS interfaces mirroring §1 exactly (Snapshot, SelfState, Mobile, Item, GumpState, TargetState, CensusEntry, WorldEvent envelope) |
| `viz/src/api.ts` | `EventSource` client (auto-resume), snapshot fetch, playback controls |
| `viz/src/store.ts` | `useSyncExternalStore` store: `{snapshot, events[500], connected, playback}` |
| `viz/src/serial.ts` | int↔`0x%08X` normalization + entity lookup across mobiles/items/names (the §1.2 wart lives here) |
| `viz/src/App.tsx` + `components/{SelfPanel,MapGrid,EntityInspector,ContainerTree,GumpViewer,EventLog,CensusPanel}.tsx` | §4 wireframes |
| `viz/src/App.css` | grid layout, dark theme |

### Replay timing

Raw captures carry no timestamps (replay.py docstring: interleaving is not
recoverable; canonical order is prelude → all C2S → all S2C). The `.jsonl`
side-log *does* carry `"t"` per entry, but its hex is truncated — so it can
supply **timing, not bytes**. Plan: `iter_replay` frames the raw streams
(byte-exact, as today) and matches framed packets against the jsonl sequence by
`(dir, id, len)` order to recover timestamps → paced replay with real cadence
and correct C2S/S2C interleave. If matching desynchronizes, fall back to a
fixed 100 pkt/s rate in canonical order and badge the replay as "approximate
timing". Either way the *set* of packets fed is identical to `replay_session`,
so all existing replay invariants hold.

### Milestones + acceptance criteria

- **M1 — replay backend + REST.** `viz_server.py --replay`, `/api/snapshot`,
  `/api/health`.
  *Acceptance*: `python harness/viz_server.py --replay 20260928_141253`, then
  `GET /api/snapshot` returns `"self"."serial" == "0x00094375"` and
  `len(mobiles) + len(items) ≥ 10`; the served body equals
  `replay_session(...).state.snapshot()` byte-for-byte (test_viz asserts this).
- **M2 — SSE events + frontend shell.** `/api/events` with envelope/seq/resume;
  Vite app with EventLog only.
  *Acceptance*: during a paced replay the EventLog appends live; seq numbers are
  gapless; killing and restarting the browser tab resumes via `Last-Event-ID`
  without duplicates; `keepalive` events arrive ~1/s on session 164548.
- **M3 — Phase A UI complete.** All 7 components.
  *Acceptance*: replay session 164548 to completion; MapGrid shows ≥10 entities
  with the player marker centered; clicking a ground item opens
  EntityInspector with serial/graphic/hue; CensusPanel lists the session's
  queried serials (0x000001D3-range NPCs); GumpViewer shows the session's
  vendor gump with layout text; SelfPanel shows name/stats/skills.
- **M4 — live mode.** `--live` embeds the proxy relay (INTERCEPTION.md runbook
  unchanged apart from step 1 becoming `python harness/viz_server.py --live`).
  *Acceptance*: full chain up (NAT + elevated client), character walks in-game;
  the player marker moves and `walk`/`walk_confirm` appear in EventLog within
  ~1 s; `/api/health` shows zero new parse failures during a 2-minute roam.
- **M5 — replay scrubber.** Playback controls (`POST /api/playback`
  {play,pause,rate,step}) + header buttons.
  *Acceptance*: pause freezes EventLog and snapshot; step feeds exactly one
  packet per click (verify a `walk` step moves the marker one tile); 8× rate
  completes session 141253 without dropped events.
- **M6 — mobile positions (runtime extension, §5.1).**
  *Acceptance*: live session — a second mobile's dot tracks its movement on
  MapGrid; replay tests extended with the new packets.

No git commits in this task; sequencing is M1→M5 (M6 independent, any time
after M3).

---

## 7. Testing plan

Style reference: `harness/test_world.py` / `test_world_replay.py` — plain
`check(name, cond, detail)` functions, `TESTS` list, `main()` returning 0/1, no
framework. New suite: `harness/test_viz.py`, invoked from `test_world.py`'s
`main()` alongside the existing two.

### Backend unit tests

- **Snapshot serialization parity** (the core contract): boot the server
  against a replay capture on an ephemeral port; `urllib` GET `/api/snapshot`;
  assert the JSON body equals `replay_session(tag).state.snapshot()` exactly —
  this is the test that guards the parity principle (no privileged fields, no
  transforms).
- **Event framing**: feed a small synthetic packet sequence (reuse the packet
  builders/fixtures style of `test_world_units.py`), connect to `/api/events`,
  read N messages; assert: `id:`/`event:`/`data:` framing parses per SSE spec;
  envelopes carry monotonically increasing `seq` and numeric `t`; `data.data`
  equals the `drain_events()` entries verbatim (including int serials).
- **Resume**: connect, read k events, disconnect; reconnect with
  `Last-Event-ID: k`; assert the replayed suffix + fresh full `state` message
  and no duplicates.
- **Dirty-tick coalescing**: feed 100 packets within one tick window; assert a
  single `state` message results and equals the final snapshot.
- **Replay timestamp matching**: on both captured sessions, assert
  `iter_replay` yields the same packet multiset as `replay_session` feeds
  (order may differ when timestamps apply; set must not).

### Replay-driven integration

- Boot-server fixture on each captured session (both `logs/session_20260928_*`
  pairs, mirroring `test_world_replay.py`): snapshot invariants
  (`self.serial == "0x00094375"`, entity counts ≥ thresholds already used by
  the replay suite), EventLog receives the full `len(result.events)` events in
  drain order, second snapshot fetch is stable (determinism).
- Live-mode smoke is manual (M4 acceptance); no automated game interaction.

### Frontend tests — deliberately light

- `vitest` unit tests for the pure helpers only: `serial.ts`
  (int→hex normalization matching `_h()`; cross-map entity lookup precedence),
  the EventLog filter predicate (type include/exclude, keepalive default-off),
  and the ContainerTree builder (ground roots, layer grouping, OPEN markers —
  built from a hand-written snapshot fixture).
- No DOM/snapshot tests of components; correctness there is demonstrated by the
  milestone acceptance checks (M3) run by a human against a replay — consistent
  with the project treating replay-driven assertions as the integration net.

---

## Appendix — dev loop cheat sheet

```bash
# --- replay (no game, no elevation) ---
python harness/viz_server.py --replay 20260928_141253 --port 8080
cd viz && npm install && npm run dev        # http://localhost:5173, /api proxied

# --- live (full INTERCEPTION.md chain) ---
python harness/viz_server.py --live --port 8080          # replaces proxy.py step
powershell -Verb RunAs restart_divert.ps1                # NAT (elevated)
powershell -Verb RunAs launch_game.ps1                   # client (elevated)
cd viz && npm run dev

# --- production-ish single process ---
cd viz && npm run build                                  # emits viz/dist/
python harness/viz_server.py --replay 20260928_164548 --serve-dist viz/dist
```

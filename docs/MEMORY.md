# MEMORY.md — the harness's durable memory (SQLite)

Status: **built 2026-09-29** (`harness/memory.py`). Decision and rationale are in docs/PLAN.md
(Phase 4, "Harness memory").

## What it is

This is everything the harness **experiences and learns while playing**:
- the event stream
- walk evidence
- harvest nodes
- loop episodes

It lives in one SQLite file, `harness/data/harness.db`, which is gitignored. Per AGENTS.md
Rule 0 (user clarification 2026-09-29), this is runtime memory, not engineering knowledge, and
it is never committed. What the repo keeps is how the memory works (this doc, the code) and the
capture fixtures that tests cite.

Why SQLite:
- It's durable: the file is on disk, and the WAL journal survives crashes.
- It's indexed and queryable with plain SQL.
- It's in the stdlib.
- It supports concurrent readers while one writer works.

The proxy's in-memory event ring (`EVENT_CAP` 5000) stays as the hot cache for state-port
readers. The store is the long-term record.

Rejected:
- Keeping JSON files. `walkmem.json` was 2D, facet-less and rewritten whole, and the harvest
  JSON needed load/save races.
- A server database (Postgres). It needs a service, which is too much for one local harness.
- DuckDB. It's analytics-first, not in the stdlib, and has a single-writer file lock.

## Schema (v1, `memory.SCHEMA`)

| Table | Key | Contents |
|---|---|---|
| `sessions` | `id`, unique `tag` | `source` (`live` from the proxy, `ingest` from a capture), `started`, `ended` |
| `events` | (`session`, `seq`) | every state-port envelope: `t`, `origin` (`world`/`proxy`), `ev`, `data` (JSON). Index (`ev`, `t`) |
| `walk_moves` | (`facet`, `x`, `y`, `z`, `dir`, `ok`) | server-confirmed moves (`ok`=1) and denies (`ok`=0) with count `n`, `first_t`, `last_t`. `z` = -32768 when unknown |
| `harvest_nodes` | (`facet`, `x`, `y`, `z`) | `graphic`, `attempts`, `successes`, `yield`, `depleted_at`, `unreachable_at`, `not_tree` |
| `harvest_attempts` | (append) | `t`, node, `outcome` (success/fail/depleted/not_tree/unreachable), `amount`. Index by node and time (regrowth and yield statistics) |
| `episodes` | `id` | `loop`, `t_start`, `t_end`, `data` (the trip row JSON) |
| `meta` | `key` | `schema_version` |

## Who writes what

- **Proxy** (`--memory-db harness/data/harness.db`; off when empty, which is what tests use):
  - `SessionTap.sink` hands every event envelope to `MemoryWriter`, a background thread that
    commits in batches every 0.25 s or 500 rows. The relay never waits on disk.
  - Walk evidence is derived from the proxy's own `step` (confirmed move, with z) and `blocked`
    (deny, with z) events. The facet comes from the world model (S2C 0xBF sub 8). Both the
    human's and the agent's walks teach it.
- **Runners** (`Memory`):
  - `loop_lumber.py` writes harvest outcomes and episodes.
  - Runners never write walk moves; the proxy already has them.
- **Ingest:** `python harness/memory.py ingest [--logdir logs] [TAG ...]` replays captures that
  aren't in the store yet through the proxy's own SessionTap (viz_feed.ReplayDriver) and
  derives the same rows. Three captures (≈29k events) take about 3.5 s.

## Who reads what

- `Memory.walk_memory(facet)`: a `nav.WalkMemory` projection. Every confirmed move is an edge.
  A move counts as blocked only while its latest deny is newer than its latest confirm, since
  doors and mobiles block only for a while.
  - `Mover` uses it as the 2D fallback planner on facets without map geometry (the rental rooms,
    facet 3).
  - The visualizer's `/api/walkmem` serves it (facet 0, same JSON format as before, cached 2 s).
- `Memory.harvest_available(...)`: tree candidate filter (not_tree, depleted/unreachable within
  the regrowth window).
- Analysis: `sqlite3 harness/data/harness.db`, e.g.
  `SELECT outcome, COUNT(*) FROM harvest_attempts GROUP BY outcome;`
- `python harness/memory.py stats`: row counts.

## Replaced (clean cutover 2026-09-29)

- `harness/data/walkmem.json` (removed from git) and `nav.py build`. Tests that used it as server
  evidence now build walk memory from the committed capture fixtures (`nav.build_from_logs`).
- `harness/data/harvestmem.json` and `harness/data/episodes/lumber.jsonl`.
- Runner and viz flags: `--memory` (runners) and `--memory-db` (proxy, viz) now point at the
  store.

## Next

- A hazard/exposure table for the per-region `h` estimate (LUMBER_LOOP.md §11).
- A state-port op to page old events from the store, so late readers don't depend on the ring.

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
capture fixtures that tests cite. It is backed up hourly to the NAS (docs/NOTES.md "Backups").

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

## Schema (v4, `memory.SCHEMA`)

| Table | Key | Contents |
|---|---|---|
| `sessions` | `id`, unique `tag` | `source` (`live` from the proxy, `ingest` from a capture), `started`, `ended` |
| `events` | (`session`, `seq`) | every state-port envelope: `t`, `origin` (`world`/`proxy`), `ev`, `data` (JSON). Index (`ev`, `t`) |
| `walk_moves` | (`facet`, `x`, `y`, `z`, `dir`, `ok`) | server-confirmed moves (`ok`=1) and denies (`ok`=0) with count `n`, `first_t`, `last_t`. `z` = -32768 when unknown |
| `harvest_nodes` | (`facet`, `x`, `y`, `z`) | `graphic`, `attempts`, `successes`, `yield`, `depleted_at`, `unreachable_at`, `not_tree` |
| `harvest_attempts` | (append) | `t`, node, `outcome` (success/fail/depleted/not_tree/unreachable), `amount`. Index by node and time (regrowth and yield statistics) |
| `episodes` | `id` | `loop`, `t_start`, `t_end`, `data` (the trip row JSON) |
| `junctures` | `id` | v2. Overseer wake-ups: `t`, `source` (runner or `ctl`), `kind` (task_done, task_failed, captcha, stuck, threat, theft_suspected, death, low_supplies, …), `severity` (info/attention/urgent), `summary`, `data`, `acked_t`. See docs/OVERSEER.md |
| `chat` | `id` | v2. The viz chat and the overseer's visible thinking: `role` (user/overseer/system), `kind` (message/thought/action), `text`, `data` (`{"via": "telegram", "message_id": N}` on user rows from the Telegram bridge) |
| `job_events` | `id` | v2. Job analytics facts other than trips: `job`, `kind` (death with `data.cause`, theft, pk_seen, flee, …), `facet`/`x`/`y`, `data` |
| `teleporters` | (`facet`, `x`, `y`) | v3. Invisible server teleporter tiles learned by stepping onto one: `to_facet`/`to_x`/`to_y`/`to_z`, `n`, `first_t`, `last_t`. The Mover plans around them (docs/OVERSEER.md) |
| `guard_points` | (`facet`, `x`, `y`) | Tiles we stood on when the server said "You are now under the protection of the town guards." (cliloc 500112) right after a one-tile step: `n`, `first_t`, `last_t`. `Mover.step` records them live; `python harness/guards.py backfill` replays the `events` table. The lumber runner's guard flight runs to them (docs/PLAN.md "Guard flight"). Added without a schema bump (`CREATE TABLE IF NOT EXISTS`) |
| `knowledge` + `knowledge_fts` | `id` | v4. The overseer's long-term memory (below): `kind`, `topic`, `content`, `tags`, `entities`, optional `facet`/`x`/`y`, `source_type`/`source_ref`, `confidence`, `importance`, `status` (active/superseded/retracted), `supersedes`/`superseded_by`, `retract_reason`, `content_hash`, `confirmations`, `created_t`/`updated_t`/`last_access_t`/`access_count`. FTS5 (porter stemming) over topic, content, tags and entities, kept in sync by triggers |
| `meta` | `key` | `schema_version`; `captcha_mode` (`human`/`auto`, missing = `human`; who answers the harvest captcha, set from the viz header, read by the runner at every captcha); overseer bus (docs/OVERSEER.md): `tasks` (running task entries), `task_stop`, `overseer_juncture_cursor`, `overseer_chat_cursor`, `overseer_heartbeat` (epoch s); Telegram bridge (docs/OVERSEER.md §8): `telegram_chat_cursor`, `telegram_juncture_cursor`, `telegram_update_offset` |

## Who writes what

- **Proxy** (`--memory-db harness/data/harness.db`; off when empty, which is what tests use):
  - `SessionTap.sink` hands every event envelope to `MemoryWriter`, a background thread that
    commits in batches every 0.25 s or 500 rows. The relay never waits on disk.
  - Walk evidence is derived from the proxy's own `step` (confirmed move, with z) and `blocked`
    (deny, with z) events. The facet comes from the world model (S2C 0xBF sub 8). Both the
    human's and the agent's walks teach it.
- **Runners** (`Memory`):
  - `loop_lumber.py` writes harvest outcomes and episodes.
  - `loop_hunt.py` writes one episode per visit (loop `hunt`: kills, gold, xp, hits lost, casts, heals,
    why it ended) and job events `kill`, `loot` (gold, xp = the corpse's gold; docs/HUNT_LOOP.md),
    `leave`, `death`, `speech_hold`/`speech_clear`. The viz Jobs page's Hunting dashboard reads them.
  - Runners never write walk moves; the proxy already has them.
- **Telegram bridge** (`harness/telegram_bridge.py`): `user` chat rows for messages from the
  phone, and its own `telegram_*` cursors in `meta`. It reads `chat` and `junctures`.
- **Ingest:** `python harness/memory.py ingest [--logdir logs] [TAG ...]` replays captures that
  aren't in the store yet through the proxy's own SessionTap (viz_feed.ReplayDriver) and
  derives the same rows. Captures from before the proxy emitted `step` events (before
  2026-09-29 16:14) get their confirmed moves from the raw packet pair instead
  (`nav.reconstruct_session`: no denies, z unknown, the facet the session ended on). All 25
  captures (70.6k events) take 7.7 s. Afterwards facet 0 has 861 tiles, 1294 edges and 33
  blocked moves, a superset of the old capture-built walk memory (849 tiles; its 4 extra tiles
  are the rental room, which the store keeps on facet 3).

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

## Knowledge: the overseer's long-term memory (v4, 2026-09-30)

The other tables record *events*. `knowledge` holds what the agent *concluded*: game knowledge
and its dealings with players and the user, kept across sessions. It is exposed as `ctl know`
(docs/OVERSEER.md §2) and implemented in `harness/knowledge.py`. The practices it follows:

- **Typed entries.**
  - `fact` (semantic)
  - `procedure` (how-to)
  - `episode` (what happened)
  - `preference` (user directives)
  - `insight` (lessons generalised from episodes)
- **Provenance on every entry.**
  - `source_type`: observed / user / doc / wiki / inferred.
  - `source_ref`: capture tag, chat#, juncture#, URL or screenshot.
  - Confidence defaults by source: user 0.95, observed 0.9, doc 0.8, wiki 0.7, inferred 0.5.
- **No silent duplicates.** The same normalised content, or ≥ 85% word overlap on the same topic,
  confirms the existing entry: confirmations += 1, and confidence closes half the gap to 1,
  capped by the new evidence's source. Every write returns `related` entries (same topic or
  ≥ 35% overlap), so the writer sees a conflict before it piles up.
- **History, not overwrites.** A content change makes a new version that supersedes the old
  one. Wrong entries are retracted with a reason. Nothing is deleted.
- **Ranked recall** (Generative Agents style), as a score:
  - 1.0 × BM25 relevance (normalised within the result set; topic weighted 3×, tags and
    entities 2×)
  - \+ 0.4 × recency (14-day half-life from the last update or access)
  - \+ 0.5 × importance/10
  - \+ 0.3 × confidence
  - \+ 0.6 × nearness (fades to 0 at 40 tiles on the same facet)

  Results count as accesses. Queries are reduced to quoted words, so FTS syntax in them is
  harmless.
- **Situational brief.** `brief` builds the query from where the agent is (position, nearby NPC
  labels, open junctures, intent, task) and always adds preferences/procedures of importance ≥ 7.
- **Maintenance.** `review` lists unconfirmed inferences, entries never recalled for 30 days, and
  topics with several active facts.

Not built: embeddings (FTS5 + stemming is enough at this size and needs no model or network), and
automatic summarisation of episodes into insights. The overseer writes insights itself.

## Next

- A hazard/exposure table for the per-region `h` estimate (LUMBER_LOOP.md §11).
- A state-port op to page old events from the store, so late readers don't depend on the ring.

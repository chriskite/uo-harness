"""Harness memory: one durable, indexed SQLite store for everything the harness
experiences and learns while playing (docs/MEMORY.md).

This is runtime data, not engineering knowledge: it is gitignored (AGENTS.md
Rule 0). Default location: harness/data/harness.db (WAL mode, so the proxy
can write while runners and the visualizer read).

Tables
  sessions          one row per proxy session (live) or ingested capture
  events            every state-port event envelope, durable (the proxy keeps
                    only a hot ring in memory)
  walk_moves        server-confirmed moves and denies with facet and z, counted,
                    first/last seen (derived from `step` / `blocked` events; both
                    the human's and the agent's walks teach it)
  harvest_nodes     per harvest node (tree): attempts, yield, depletion,
                    reachability, not-a-tree
  harvest_attempts  every harvest attempt outcome (regrowth / yield statistics)
  episodes          one row per loop trip (phase times, human texture, results)
  junctures         moments that should wake the overseer AI (a task stuck,
                    aborted or finished, a captcha, a threat, a theft
                    suspicion); acked once the overseer has handled them
  chat              the viz chat and the overseer's visible thinking:
                    role user|overseer|system, kind message|thought|action|memory
  meta              key/value: schema_version, overseer_heartbeat, captcha_mode
                    (human|auto, default human; set from the viz header)
  job_events        job analytics facts other than trips: death, theft
                    (suspected loss), pk_seen, flee, mob_attack, resurrect, ...
  teleporters       invisible server teleporter tiles learned by walking onto
                    one: source tile -> where it put us (the planner avoids them)
  knowledge         the overseer's long-term memory (harness/knowledge.py): facts,
                    procedures, episodes, user preferences, insights with
                    provenance, confidence, importance, versions; FTS5-indexed
  lumber_spots      v5. Lumber spots the optimizer chooses between (lumber_opt.py):
                    rows the overseer added or `discover` proposed, and status
                    overrides of the seed spots in harness/data/lumber_spots.json
  prices            v5. Observed market prices (gp) by item key, append-only; the
                    newest per item counts (hatchet economics, later gold/hour)

Writers
  - the proxy: MemoryWriter, a background thread with batched commits, fed from
    SessionTap events; it never blocks the relay
  - runners: Memory directly (harvest nodes/attempts, episodes)
  - `python harness/memory.py ingest [--logdir logs]`: replays captures not yet
    in the store through the proxy's own SessionTap (viz_feed.ReplayDriver)

Readers
  - Memory.walk_memory(facet): nav.WalkMemory projection for the 2D fallback
    planner and the visualizer's walk layer
  - plain SQL for analysis (sqlite3 harness/data/harness.db)
"""
import argparse
import glob
import json
import os
import queue
import re
import sqlite3
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nav  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "harness", "data", "harness.db")
SCHEMA_VERSION = 5
UNKNOWN_Z = -32768

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sessions(
    id INTEGER PRIMARY KEY, tag TEXT UNIQUE NOT NULL, source TEXT NOT NULL,
    started REAL, ended REAL);
CREATE TABLE IF NOT EXISTS events(
    session INTEGER NOT NULL, seq INTEGER NOT NULL, t REAL, origin TEXT, ev TEXT,
    data TEXT NOT NULL, PRIMARY KEY(session, seq)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS events_ev_t ON events(ev, t);
CREATE TABLE IF NOT EXISTS walk_moves(
    facet INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, z INTEGER NOT NULL,
    dir INTEGER NOT NULL, ok INTEGER NOT NULL, n INTEGER NOT NULL,
    first_t REAL, last_t REAL,
    PRIMARY KEY(facet, x, y, z, dir, ok)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS harvest_nodes(
    facet INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, z INTEGER NOT NULL,
    graphic INTEGER, attempts INTEGER NOT NULL DEFAULT 0, successes INTEGER NOT NULL DEFAULT 0,
    yield INTEGER NOT NULL DEFAULT 0, depleted_at REAL, unreachable_at REAL,
    not_tree INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(facet, x, y, z)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS harvest_attempts(
    t REAL NOT NULL, facet INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL,
    z INTEGER NOT NULL, outcome TEXT NOT NULL, amount INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS harvest_attempts_node ON harvest_attempts(facet, x, y, t);
CREATE TABLE IF NOT EXISTS episodes(
    id INTEGER PRIMARY KEY, loop TEXT NOT NULL, t_start REAL, t_end REAL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS junctures(
    id INTEGER PRIMARY KEY, t REAL NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL,
    severity TEXT NOT NULL, summary TEXT NOT NULL, data TEXT NOT NULL, acked_t REAL);
CREATE INDEX IF NOT EXISTS junctures_open ON junctures(acked_t, id);
CREATE TABLE IF NOT EXISTS chat(
    id INTEGER PRIMARY KEY, t REAL NOT NULL, role TEXT NOT NULL, kind TEXT NOT NULL,
    text TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS job_events(
    id INTEGER PRIMARY KEY, t REAL NOT NULL, job TEXT NOT NULL, kind TEXT NOT NULL,
    facet INTEGER, x INTEGER, y INTEGER, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS job_events_job_t ON job_events(job, t);
CREATE TABLE IF NOT EXISTS teleporters(
    facet INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL,
    to_facet INTEGER, to_x INTEGER NOT NULL, to_y INTEGER NOT NULL, to_z INTEGER,
    n INTEGER NOT NULL, first_t REAL NOT NULL, last_t REAL NOT NULL,
    PRIMARY KEY (facet, x, y));
CREATE TABLE IF NOT EXISTS guard_points(
    facet INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL,
    n INTEGER NOT NULL, first_t REAL NOT NULL, last_t REAL NOT NULL,
    PRIMARY KEY (facet, x, y));
CREATE TABLE IF NOT EXISTS knowledge(
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, topic TEXT NOT NULL, content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '', entities TEXT NOT NULL DEFAULT '[]',
    facet INTEGER, x INTEGER, y INTEGER,
    source_type TEXT NOT NULL, source_ref TEXT,
    confidence REAL NOT NULL, importance INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active', supersedes INTEGER, superseded_by INTEGER, retract_reason TEXT,
    content_hash TEXT NOT NULL, confirmations INTEGER NOT NULL DEFAULT 0,
    created_t REAL NOT NULL, updated_t REAL NOT NULL, last_access_t REAL, access_count INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS knowledge_status_kind ON knowledge(status, kind);
CREATE INDEX IF NOT EXISTS knowledge_hash ON knowledge(content_hash);
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    topic, content, tags, entities, content='knowledge', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS knowledge_ai AFTER INSERT ON knowledge BEGIN
    INSERT INTO knowledge_fts(rowid, topic, content, tags, entities)
    VALUES (new.id, new.topic, new.content, new.tags, new.entities);
END;
CREATE TRIGGER IF NOT EXISTS knowledge_ad AFTER DELETE ON knowledge BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, topic, content, tags, entities)
    VALUES ('delete', old.id, old.topic, old.content, old.tags, old.entities);
END;
CREATE TRIGGER IF NOT EXISTS knowledge_au AFTER UPDATE OF topic, content, tags, entities ON knowledge BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, topic, content, tags, entities)
    VALUES ('delete', old.id, old.topic, old.content, old.tags, old.entities);
    INSERT INTO knowledge_fts(rowid, topic, content, tags, entities)
    VALUES (new.id, new.topic, new.content, new.tags, new.entities);
END;
-- semantic recall (knowledge.py): one embedding of "topic: content" per entry; hash = sha1(model + text)
CREATE TABLE IF NOT EXISTS knowledge_vec(
    id INTEGER PRIMARY KEY, hash TEXT NOT NULL, vec BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS lumber_spots(
    id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL, reason TEXT,
    source TEXT NOT NULL, created_t REAL NOT NULL, updated_t REAL NOT NULL);
CREATE TABLE IF NOT EXISTS prices(
    id INTEGER PRIMARY KEY, item TEXT NOT NULL, price_gp REAL NOT NULL, t REAL NOT NULL,
    source TEXT NOT NULL, note TEXT);
CREATE INDEX IF NOT EXISTS prices_item_t ON prices(item, t);
"""


def connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path, timeout=30, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    con.execute("INSERT OR REPLACE INTO meta VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
    con.commit()
    return con


# ----------------------------------------------------------------------- derive
def walk_rows(env: dict, facet: int | None, t: float):
    """walk_moves rows implied by one event envelope:
    [(facet, x, y, z, dir, ok, t)]. `step` (proxy, server-confirmed) and
    `blocked` (proxy, server deny) both carry the tile moved from."""
    if env.get("origin") != "proxy":
        return []
    d = env["data"]
    f = 0 if facet is None else facet
    if d.get("ev") == "step":
        a, b = tuple(d["from"]), tuple(d["to"])
        try:
            direction = nav.direction(a, b)
        except ValueError:
            return []               # a teleport, not a step
        z = d.get("z")
        return [(f, a[0], a[1], UNKNOWN_Z if z is None else z, direction, 1, t)]
    if d.get("ev") == "blocked":
        a = d["from"]
        z = d.get("z")
        return [(f, a[0], a[1], UNKNOWN_Z if z is None else z, d["dir"], 0, t)]
    return []


def _upsert_walk(cur, rows):
    cur.executemany(
        "INSERT INTO walk_moves VALUES(?,?,?,?,?,?,1,?,?) ON CONFLICT(facet,x,y,z,dir,ok) "
        "DO UPDATE SET n = n + 1, last_t = excluded.last_t",
        [(f, x, y, z, d, ok, t, t) for f, x, y, z, d, ok, t in rows])


# ----------------------------------------------------------------------- memory
class Memory:
    """Runner-side access (single thread)."""

    def __init__(self, path: str = DEFAULT_DB):
        self.path = path
        self.con = connect(path)

    def close(self):
        self.con.close()

    # -- walking -----------------------------------------------------------
    def walk_memory(self, facet: int = 0) -> nav.WalkMemory:
        """2D projection for nav.plan / the viz walk layer: every confirmed move
        is an edge; a move is blocked when its latest deny is newer than its
        latest confirm (doors and mobiles block only for a while)."""
        mem = nav.WalkMemory()
        ok_last, deny_last = {}, {}
        for x, y, d, ok, last in self.con.execute(
                "SELECT x, y, dir, ok, MAX(last_t) FROM walk_moves WHERE facet = ? "
                "GROUP BY x, y, dir, ok", (facet,)):
            (ok_last if ok else deny_last)[(x, y, d)] = last or 0.0
        for (x, y, d) in ok_last:
            mem.add_step((x, y), nav.step((x, y), d))
        for key, last in deny_last.items():
            if last > ok_last.get(key, -1.0):
                mem.add_blocked(key[:2], key[2])
        return mem

    def walkmem_json(self, facet: int = 0) -> bytes:
        """The visualizer's walk layer file format (nav.WalkMemory.to_json)."""
        return self.walk_memory(facet).to_json().encode()

    # -- harvesting ----------------------------------------------------------
    def harvest_node(self, facet, x, y, z) -> dict | None:
        row = self.con.execute(
            "SELECT graphic, attempts, successes, yield, depleted_at, unreachable_at, not_tree "
            "FROM harvest_nodes WHERE facet=? AND x=? AND y=? AND z=?", (facet, x, y, z)).fetchone()
        if row is None:
            return None
        keys = ("graphic", "attempts", "successes", "yield", "depleted_at", "unreachable_at", "not_tree")
        return dict(zip(keys, row))

    def harvest_available(self, facet, x, y, z, regrow_s, now) -> bool:
        r = self.harvest_node(facet, x, y, z)
        if r is None:
            return True
        if r["not_tree"]:
            return False
        return all(r[k] is None or now - r[k] >= regrow_s for k in ("depleted_at", "unreachable_at"))

    def harvest_record(self, facet, x, y, z, graphic, outcome, amount=0, t=None):
        """One attempt outcome: success/fail/depleted/not_tree/unreachable."""
        t = time.time() if t is None else t
        c = self.con
        c.execute("INSERT OR IGNORE INTO harvest_nodes(facet, x, y, z, graphic) VALUES(?,?,?,?,?)",
                  (facet, x, y, z, graphic))
        if outcome in ("success", "fail"):
            c.execute("UPDATE harvest_nodes SET attempts = attempts + 1, successes = successes + ?, "
                      "yield = yield + ? WHERE facet=? AND x=? AND y=? AND z=?",
                      (1 if outcome == "success" else 0, amount, facet, x, y, z))
        elif outcome == "depleted":
            c.execute("UPDATE harvest_nodes SET depleted_at=? WHERE facet=? AND x=? AND y=? AND z=?",
                      (t, facet, x, y, z))
        elif outcome == "unreachable":
            c.execute("UPDATE harvest_nodes SET unreachable_at=? WHERE facet=? AND x=? AND y=? AND z=?",
                      (t, facet, x, y, z))
        elif outcome == "not_tree":
            c.execute("UPDATE harvest_nodes SET not_tree=1 WHERE facet=? AND x=? AND y=? AND z=?",
                      (facet, x, y, z))
        c.execute("INSERT INTO harvest_attempts VALUES(?,?,?,?,?,?,?)", (t, facet, x, y, z, outcome, amount))
        c.commit()

    # -- episodes --------------------------------------------------------------
    def episode(self, loop: str, row: dict):
        self.con.execute("INSERT INTO episodes(loop, t_start, t_end, data) VALUES(?,?,?,?)",
                         (loop, row.get("t_start"), row.get("t_end"), json.dumps(row)))
        self.con.commit()

    def episodes(self, loop: str):
        return [json.loads(d) for (d,) in self.con.execute(
            "SELECT data FROM episodes WHERE loop=? ORDER BY id", (loop,))]

    # -- overseer bus (docs/OVERSEER.md) -----------------------------------------
    SEVERITIES = ("info", "attention", "urgent")

    def juncture(self, source: str, kind: str, summary: str, severity: str = "attention",
                 data: dict | None = None, t: float | None = None) -> int:
        """Post a juncture for the overseer (never blocks the caller for long)."""
        if severity not in self.SEVERITIES:
            raise ValueError(f"severity must be one of {self.SEVERITIES}")
        cur = self.con.execute(
            "INSERT INTO junctures(t, source, kind, severity, summary, data) VALUES(?,?,?,?,?,?)",
            (time.time() if t is None else t, source, kind, severity, summary, json.dumps(data or {})))
        self.con.commit()
        return cur.lastrowid

    def junctures(self, after_id: int = 0, open_only: bool = False, limit: int = 100,
                  newest: bool = False) -> list[dict]:
        """Junctures with id > after_id, oldest first; `newest`: the last `limit` of them
        (still oldest first)."""
        q = ("SELECT id, t, source, kind, severity, summary, data, acked_t FROM junctures WHERE id > ?"
             + (" AND acked_t IS NULL" if open_only else "")
             + (" ORDER BY id DESC LIMIT ?" if newest else " ORDER BY id LIMIT ?"))
        keys = ("id", "t", "source", "kind", "severity", "summary", "data", "acked_t")
        out = []
        rows = self.con.execute(q, (after_id, limit)).fetchall()
        for row in (reversed(rows) if newest else rows):
            d = dict(zip(keys, row))
            d["data"] = json.loads(d["data"])
            out.append(d)
        return out

    def juncture_ack(self, jid: int, t: float | None = None) -> bool:
        cur = self.con.execute("UPDATE junctures SET acked_t=? WHERE id=? AND acked_t IS NULL",
                               (time.time() if t is None else t, jid))
        self.con.commit()
        return cur.rowcount == 1

    CHAT_ROLES = ("user", "overseer", "system")
    CHAT_KINDS = ("message", "thought", "action", "memory")

    def chat_post(self, role: str, text: str, kind: str = "message", data: dict | None = None,
                  t: float | None = None) -> int:
        if role not in self.CHAT_ROLES or kind not in self.CHAT_KINDS:
            raise ValueError(f"role in {self.CHAT_ROLES}, kind in {self.CHAT_KINDS}")
        if not text:
            raise ValueError("empty chat text")
        cur = self.con.execute("INSERT INTO chat(t, role, kind, text, data) VALUES(?,?,?,?,?)",
                               (time.time() if t is None else t, role, kind, text, json.dumps(data or {})))
        self.con.commit()
        return cur.lastrowid

    def chat(self, after_id: int = 0, limit: int = 200, role: str | None = None) -> list[dict]:
        """Chat rows with id > after_id, oldest first (optionally one role)."""
        q = "SELECT id, t, role, kind, text, data FROM chat WHERE id > ?"
        args: list = [after_id]
        if role is not None:
            q += " AND role = ?"
            args.append(role)
        q += " ORDER BY id LIMIT ?"
        args.append(limit)
        keys = ("id", "t", "role", "kind", "text", "data")
        out = []
        for row in self.con.execute(q, args):
            d = dict(zip(keys, row))
            d["data"] = json.loads(d["data"])
            out.append(d)
        return out

    # -- settings (meta) --------------------------------------------------------------
    CAPTCHA_MODES = ("human", "auto")

    def captcha_mode(self) -> str:
        """Who answers the harvest captcha: "human" (default; the runner pauses
        and beeps until it's solved in the client) or "auto" (harness/captcha.py
        reads the layout and the runner answers). Toggled from the viz header."""
        row = self.con.execute("SELECT value FROM meta WHERE key = 'captcha_mode'").fetchone()
        return row[0] if row and row[0] in self.CAPTCHA_MODES else "human"

    def set_captcha_mode(self, mode: str):
        if mode not in self.CAPTCHA_MODES:
            raise ValueError(f"captcha mode must be one of {self.CAPTCHA_MODES}")
        self.con.execute("INSERT OR REPLACE INTO meta VALUES('captcha_mode', ?)", (mode,))
        self.con.commit()

    # -- job analytics ------------------------------------------------------------
    def job_event(self, job: str, kind: str, data: dict | None = None, facet=None, x=None, y=None,
                  t: float | None = None) -> int:
        cur = self.con.execute(
            "INSERT INTO job_events(t, job, kind, facet, x, y, data) VALUES(?,?,?,?,?,?,?)",
            (time.time() if t is None else t, job, kind, facet, x, y, json.dumps(data or {})))
        self.con.commit()
        return cur.lastrowid

    def job_events(self, job: str, since: float = 0.0) -> list[dict]:
        keys = ("id", "t", "job", "kind", "facet", "x", "y", "data")
        out = []
        for row in self.con.execute("SELECT id, t, job, kind, facet, x, y, data FROM job_events "
                                    "WHERE job = ? AND t >= ? ORDER BY t, id", (job, since)):
            d = dict(zip(keys, row))
            d["data"] = json.loads(d["data"])
            out.append(d)
        return out

    USES_LABEL = re.compile(r"\((\d+) uses remaining\)")

    def uses_seen(self, serial: int) -> dict | None:
        """{n, t} of the newest "(N uses remaining)" label the server sent for item
        `serial` (a single click by the overseer or the human; the proxy stores it as a
        speech_heard event), or None when it was never clicked."""
        for t, data in self.con.execute(
                "SELECT t, data FROM events WHERE ev = 'speech_heard' AND data LIKE ? ORDER BY t DESC LIMIT 1",
                (f'%"serial": {int(serial)},%uses remaining%',)):
            m = self.USES_LABEL.search(json.loads(data).get("text") or "")
            if m:
                return {"n": int(m[1]), "t": round(t, 1)}
        return None

    # -- teleporters ----------------------------------------------------------------
    def teleporter_record(self, facet, x, y, to_facet, to_x, to_y, to_z, t: float | None = None):
        """Walking onto (facet, x, y) put us at (to_facet, to_x, to_y, to_z)."""
        t = time.time() if t is None else t
        self.con.execute(
            "INSERT INTO teleporters(facet, x, y, to_facet, to_x, to_y, to_z, n, first_t, last_t) "
            "VALUES(?,?,?,?,?,?,?,1,?,?) ON CONFLICT(facet, x, y) DO UPDATE SET "
            "to_facet=excluded.to_facet, to_x=excluded.to_x, to_y=excluded.to_y, to_z=excluded.to_z, "
            "n=n+1, last_t=excluded.last_t",
            (0 if facet is None else facet, x, y, to_facet, to_x, to_y, to_z, t, t))
        self.con.commit()

    def teleporters(self, facet) -> dict:
        """{(x, y): (to_facet, to_x, to_y, to_z)} known teleporter tiles on facet."""
        return {(x, y): (tf, tx, ty, tz) for x, y, tf, tx, ty, tz in self.con.execute(
            "SELECT x, y, to_facet, to_x, to_y, to_z FROM teleporters WHERE facet = ?",
            (0 if facet is None else facet,))}

    # -- guard zones ----------------------------------------------------------------
    def guard_point_record(self, facet, x, y, t: float | None = None):
        """(facet, x, y) is a tile just inside a town-guard zone (guards.py)."""
        t = time.time() if t is None else t
        self.con.execute(
            "INSERT INTO guard_points(facet, x, y, n, first_t, last_t) VALUES(?,?,?,1,?,?) "
            "ON CONFLICT(facet, x, y) DO UPDATE SET n=n+1, last_t=excluded.last_t",
            (0 if facet is None else facet, x, y, t, t))
        self.con.commit()

    def guard_points(self, facet) -> set:
        """{(x, y)} known tiles inside town-guard zones on facet."""
        return {(x, y) for x, y in self.con.execute(
            "SELECT x, y FROM guard_points WHERE facet = ?", (0 if facet is None else facet,))}

    # -- lumber spots and prices (lumber_opt.py, docs/LUMBER_LOOP.md §6) ---------------
    SPOT_STATUSES = ("active", "candidate", "disabled")

    def lumber_spot_rows(self) -> list[dict]:
        """Every lumber_spots row: {id, status, data, reason, source, created_t, updated_t}."""
        keys = ("id", "status", "data", "reason", "source", "created_t", "updated_t")
        out = []
        for row in self.con.execute("SELECT id, status, data, reason, source, created_t, updated_t "
                                    "FROM lumber_spots ORDER BY id"):
            d = dict(zip(keys, row))
            d["data"] = json.loads(d["data"])
            out.append(d)
        return out

    def lumber_spot_put(self, spot_id: str, status: str, data: dict, source: str,
                        reason: str | None = None, t: float | None = None):
        """Insert or replace a spot row (data {} = a status override of a seed spot);
        created_t survives replacement."""
        if status not in self.SPOT_STATUSES:
            raise ValueError(f"status must be one of {self.SPOT_STATUSES}")
        t = time.time() if t is None else t
        self.con.execute(
            "INSERT INTO lumber_spots(id, status, data, reason, source, created_t, updated_t) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
            "data=excluded.data, reason=excluded.reason, source=excluded.source, updated_t=excluded.updated_t",
            (spot_id, status, json.dumps(data), reason, source, t, t))
        self.con.commit()

    def lumber_spot_delete(self, spot_id: str):
        """Remove a spot row (`ctl lumber discover` replacing its own earlier candidates)."""
        self.con.execute("DELETE FROM lumber_spots WHERE id = ?", (spot_id,))
        self.con.commit()

    def price_record(self, item: str, price_gp: float, source: str, note: str | None = None,
                     t: float | None = None) -> int:
        cur = self.con.execute("INSERT INTO prices(item, price_gp, t, source, note) VALUES(?,?,?,?,?)",
                               (item, float(price_gp), time.time() if t is None else t, source, note))
        self.con.commit()
        return cur.lastrowid

    def prices(self) -> dict:
        """{item: {price_gp, t, source, note}}, the newest observation per item."""
        out = {}
        for item, gp, t, source, note in self.con.execute(
                "SELECT item, price_gp, t, source, note FROM prices ORDER BY t, id"):
            out[item] = {"price_gp": gp, "t": t, "source": source, "note": note}
        return out


# ----------------------------------------------------------------------- writer
class MemoryWriter:
    """Proxy-side durable event sink. `record(tag, env, facet)` only enqueues;
    a daemon thread inserts events and derived walk moves, committing in
    batches (every BATCH_S or BATCH_N rows). The relay never waits on disk."""

    BATCH_S = 0.25
    BATCH_N = 500

    def __init__(self, path: str):
        self.path = path
        self.q = queue.SimpleQueue()
        self.errors = 0
        self._sessions = {}
        self._thread = threading.Thread(target=self._run, name="memory-writer", daemon=True)
        self._thread.start()

    def open_session(self, tag: str, source: str = "live"):
        self.q.put(("open", tag, source, time.time()))

    def close_session(self, tag: str):
        self.q.put(("close", tag, time.time()))

    def record(self, tag: str, env: dict, facet: int | None):
        self.q.put(("event", tag, env, facet))

    def flush(self, timeout: float = 5.0):
        """Block until everything enqueued so far is committed (tests, shutdown)."""
        done = threading.Event()
        self.q.put(("flush", done))
        done.wait(timeout)

    def _session_id(self, cur, tag, source="live", started=None):
        sid = self._sessions.get(tag)
        if sid is None:
            cur.execute("INSERT OR IGNORE INTO sessions(tag, source, started) VALUES(?,?,?)",
                        (tag, source, started))
            sid = cur.execute("SELECT id FROM sessions WHERE tag=?", (tag,)).fetchone()[0]
            self._sessions[tag] = sid
        return sid

    def _run(self):
        con = connect(self.path)
        cur = con.cursor()
        pending, last_commit = 0, time.monotonic()
        while True:
            try:
                item = self.q.get(timeout=self.BATCH_S)
            except queue.Empty:
                item = None
            try:
                if item is not None:
                    kind = item[0]
                    if kind == "open":
                        self._session_id(cur, item[1], item[2], item[3])
                    elif kind == "close":
                        cur.execute("UPDATE sessions SET ended=? WHERE tag=?", (item[2], item[1]))
                    elif kind == "event":
                        _, tag, env, facet = item
                        sid = self._session_id(cur, tag)
                        cur.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)",
                                    (sid, env["seq"], env.get("t"), env.get("origin"),
                                     env["data"].get("ev"), json.dumps(env["data"])))
                        rows = walk_rows(env, facet, env.get("t") or time.time())
                        if rows:
                            _upsert_walk(cur, rows)
                    pending += 1
                if pending and (item is None or pending >= self.BATCH_N
                                or time.monotonic() - last_commit >= self.BATCH_S
                                or item[0] in ("flush", "close")):
                    con.commit()
                    pending, last_commit = 0, time.monotonic()
                if item is not None and item[0] == "flush":
                    item[1].set()
            except sqlite3.Error:
                self.errors += 1


# ----------------------------------------------------------------------- ingest
def ingest(db: str, logdir: str, tags=None, verbose=True) -> dict:
    """Replay captures not yet in the store (by tag) through the proxy's own
    SessionTap and persist their events and walk moves."""
    import viz_feed
    con = connect(db)
    known = {t for (t,) in con.execute("SELECT tag FROM sessions")}
    if tags is None:
        tags = sorted(os.path.basename(p)[len("session_"):-len(".s2c.raw")]
                      for p in glob.glob(os.path.join(logdir, "session_*.s2c.raw")))
    out = {"sessions": 0, "events": 0, "walk_rows": 0, "skipped": 0}
    for tag in tags:
        if tag in known:
            out["skipped"] += 1
            continue
        try:
            drv = viz_feed.ReplayDriver(tag, logdir)
        except (OSError, ValueError) as e:
            if verbose:
                print(f"  {tag}: not replayable ({e})")
            out["skipped"] += 1
            continue
        cur = con.cursor()
        cur.execute("INSERT INTO sessions(tag, source, started) VALUES(?,?,?)", (tag, "ingest", drv.t0))
        sid = cur.lastrowid
        cursor, n_ev, n_walk = 0, 0, 0
        while drv.position < len(drv.items):
            drv._advance(None, 1)
            tap = drv.tap
            facet = tap.world.state.self.map
            for env in tap.events[max(cursor - tap.events_base, 0):]:
                cur.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)",
                            (sid, env["seq"], env.get("t"), env.get("origin"),
                             env["data"].get("ev"), json.dumps(env["data"])))
                rows = walk_rows(env, facet, env.get("t") or 0.0)
                if rows:
                    _upsert_walk(cur, rows)
                    n_walk += len(rows)
                n_ev += 1
            cursor = tap.events_base + len(tap.events)
        if n_walk == 0:
            n_walk = _ingest_raw_walks(cur, logdir, tag, drv.tap.world.state.self.map, drv.t0)
        cur.execute("UPDATE sessions SET ended=? WHERE id=?", (drv.now, sid))
        con.commit()
        out["sessions"] += 1
        out["events"] += n_ev
        out["walk_rows"] += n_walk
        if verbose:
            print(f"  {tag}: {n_ev} events, {n_walk} walk rows ({drv.order} order)")
    con.close()
    return out


def _ingest_raw_walks(cur, logdir, tag, facet, t) -> int:
    """Captures from before the proxy emitted `step` events: confirmed moves
    recovered from the raw packet pair (nav.reconstruct_session: walk confirms
    matched to C2S walks; no denies, no z). One facet per capture, the one the
    session ended on: no such capture visits a second facet (rooms came later)."""
    try:
        with open(os.path.join(logdir, f"session_{tag}.c2s.raw"), "rb") as f:
            c2s = f.read()
        with open(os.path.join(logdir, f"session_{tag}.s2c.raw"), "rb") as f:
            s2c = f.read()
    except OSError:
        return 0
    if not c2s:
        return 0
    mem = nav.reconstruct_session(c2s, s2c)
    f = 0 if facet is None else facet
    rows = [(f, a[0], a[1], UNKNOWN_Z, nav.direction(a, b), 1, t or 0.0) for a, b in sorted(mem.edges)]
    _upsert_walk(cur, rows)
    return len(rows)


def stats(db: str) -> dict:
    con = connect(db)
    out = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
           for t in ("sessions", "events", "walk_moves", "harvest_nodes", "harvest_attempts", "episodes",
                     "junctures", "chat", "job_events", "teleporters", "knowledge", "lumber_spots", "prices")}
    con.close()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="harness memory (SQLite)")
    ap.add_argument("--db", default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("ingest", help="replay captures not yet in the store")
    i.add_argument("--logdir", default=os.path.join(ROOT, "logs"))
    i.add_argument("tags", nargs="*")
    sub.add_parser("stats", help="row counts")
    a = ap.parse_args(argv)
    if a.cmd == "ingest":
        print(ingest(a.db, a.logdir, a.tags or None))
    else:
        print(stats(a.db))
    return 0


if __name__ == "__main__":
    sys.exit(main())

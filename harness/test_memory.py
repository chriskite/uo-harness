"""Tests for harness/memory.py: the durable harness memory (docs/MEMORY.md).

Covers what a consumer relies on:
  * walk evidence derivation from proxy `step` / `blocked` envelopes
  * the walk projection: deny vs confirm recency, facet isolation
  * harvest node availability transitions over the regrowth window
  * MemoryWriter (the proxy's sink) persists events and walk moves
  * capture ingest: matches the capture's known walk (session_20260929_163420:
    36 proxy `step` events, VISUALIZER.md M1) and is idempotent per tag
  * v6 characters: a v5 file migrates in place, sessions learn who logged in,
    a character-scoped reader sees its own rows plus the unscoped ones, and
    backfill-identity fills older sessions from events, the raw capture or episodes

Run: python harness/test_memory.py   (offline, a few seconds)
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import memory  # noqa: E402
import nav  # noqa: E402
from memory import UNKNOWN_Z, Memory, MemoryWriter  # noqa: E402

TAG = "20260929_163420"
FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def tmpdb() -> str:
    return os.path.join(tempfile.mkdtemp(), "harness.db")


def proxy_env(data):
    return {"origin": "proxy", "data": data}


def test_walk_rows():
    print("walk_rows")
    check("step -> confirmed move from the origin tile, with z",
          memory.walk_rows(proxy_env({"ev": "step", "from": [10, 10], "to": [11, 9], "z": 5}), 0, 1.0)
          == [(0, 10, 10, 5, 1, 1, 1.0)])
    check("blocked -> deny with the facet given",
          memory.walk_rows(proxy_env({"ev": "blocked", "from": [39, 66], "dir": 4, "z": 1}), 3, 2.0)
          == [(3, 39, 66, 1, 4, 0, 2.0)])
    check("missing z -> UNKNOWN_Z; unknown facet -> 0",
          memory.walk_rows(proxy_env({"ev": "blocked", "from": [1, 1], "dir": 0}), None, 0.0)
          == [(0, 1, 1, UNKNOWN_Z, 0, 0, 0.0)])
    check("a teleport (non-adjacent step) is not a move",
          memory.walk_rows(proxy_env({"ev": "step", "from": [1935, 2580], "to": [39, 65]}), 0, 0.0) == [])
    check("world-origin events are not walk evidence",
          memory.walk_rows({"origin": "world", "data": {"ev": "step", "from": [1, 1], "to": [2, 1]}}, 0, 0.0) == [])


def test_projection():
    print("walk projection")
    path = tmpdb()
    con = memory.connect(path)
    cur = con.cursor()
    memory._upsert_walk(cur, [
        (0, 10, 10, 0, 2, 1, 1.0),      # confirmed E
        (0, 10, 10, 0, 2, 0, 5.0),      # later denied E (door shut) -> blocked
        (0, 20, 20, 0, 4, 0, 1.0),      # denied S
        (0, 20, 20, 0, 4, 1, 9.0),      # later confirmed S -> not blocked
        (0, 20, 20, 7, 4, 0, 3.0),      # deny at another z, still older than the confirm
        (3, 39, 66, 1, 4, 1, 1.0),      # room facet
    ])
    memory._upsert_walk(cur, [(0, 10, 10, 0, 2, 1, 2.0)])   # repeat confirm: counted, not duplicated
    con.commit()
    n = con.execute("SELECT n FROM walk_moves WHERE facet=0 AND x=10 AND y=10 AND ok=1").fetchone()[0]
    con.close()
    check("repeated move increments n", n == 2, str(n))
    m = Memory(path)
    w0, w3 = m.walk_memory(0), m.walk_memory(3)
    check("deny newer than the last confirm blocks", ((10, 10), 2) in w0.blocked, str(w0.blocked))
    check("confirm newer than every deny unblocks (any z)", ((20, 20), 4) not in w0.blocked, str(w0.blocked))
    check("confirmed moves are edges", nav.step((10, 10), 2) in w0.tiles and (20, 21) in w0.tiles)
    check("facets are isolated", (39, 66) not in w0.tiles and (39, 66) in w3.tiles and len(w3.tiles) == 2,
          str(sorted(w3.tiles)))
    recent = m.walk_memory(0, since=4.0)
    check("since keeps only moves last seen then or later",
          (20, 21) in recent.tiles and nav.step((10, 10), 2) not in recent.tiles and ((10, 10), 2) in recent.blocked,
          str((sorted(recent.tiles), recent.blocked)))
    m.close()


def test_harvest():
    print("harvest nodes")
    m = Memory(tmpdb())
    node = (0, 1935, 2605, 0, 0x0CE0)
    check("unknown tree is available", m.harvest_available(0, 1935, 2605, 0, 600, 1000.0))
    m.harvest_record(*node, "success", 10, t=1000.0)
    m.harvest_record(*node, "fail", 0, t=1010.0)
    m.harvest_record(*node, "depleted", t=1020.0)
    r = m.harvest_node(0, 1935, 2605, 0)
    check("attempts/successes/yield accumulate; depletion stamped",
          r["attempts"] == 2 and r["successes"] == 1 and r["yield"] == 10 and r["depleted_at"] == 1020.0, str(r))
    check("depleted tree unavailable inside the regrowth window",
          not m.harvest_available(0, 1935, 2605, 0, 600, 1619.0))
    check("available again at the window's end", m.harvest_available(0, 1935, 2605, 0, 600, 1620.0))
    m.harvest_record(0, 1940, 2600, 0, 0x0CE0, "unreachable", t=1000.0)
    check("unreachable tree skipped inside the window, retried after",
          not m.harvest_available(0, 1940, 2600, 0, 600, 1100.0)
          and m.harvest_available(0, 1940, 2600, 0, 600, 1700.0))
    m.harvest_record(0, 1941, 2601, 0, 0x0CE0, "not_tree", t=1000.0)
    check("not-a-tree is never available again", not m.harvest_available(0, 1941, 2601, 0, 0, 1e12))
    check("same tile on another facet is a different node", m.harvest_available(1, 1935, 2605, 0, 600, 1100.0))
    m.harvest_record(0, 1942, 2602, 0, 0x0CE0, "nothing_near", t=1000.0)
    check("a tree in reach of a Smart Harvest 'nothing nearby' is out of wood for the regrowth window",
          not m.harvest_available(0, 1942, 2602, 0, 600, 1599.0)
          and m.harvest_available(0, 1942, 2602, 0, 600, 1600.0)
          and m.harvest_node(0, 1942, 2602, 0)["depleted_at"] == 1000.0)
    n = m.con.execute("SELECT COUNT(*) FROM harvest_attempts").fetchone()[0]
    check("every outcome logged as an attempt row", n == 6, str(n))
    m.episode("lumber", {"t_start": 1.0, "t_end": 2.0, "logs": 10})
    m.episode("errand", {"t_start": 3.0})
    check("episodes are per loop, in order", m.episodes("lumber") == [{"t_start": 1.0, "t_end": 2.0, "logs": 10}])
    m.close()


def test_writer():
    print("MemoryWriter (proxy sink)")
    path = tmpdb()
    w = MemoryWriter(path)
    w.open_session("t1")
    w.record("t1", {"seq": 0, "t": 1.0, "origin": "proxy",
                    "data": {"ev": "step", "from": [5, 5], "to": [5, 4], "z": 0}}, 0)
    w.record("t1", {"seq": 1, "t": 2.0, "origin": "proxy",
                    "data": {"ev": "blocked", "from": [5, 4], "dir": 0, "z": 0}}, 0)
    w.record("t1", {"seq": 2, "t": 3.0, "origin": "world", "data": {"ev": "speech_heard", "text": "hi"}}, 0)
    w.close_session("t1")
    w.flush()
    m = Memory(path)
    evs = m.con.execute("SELECT seq, origin, ev FROM events ORDER BY seq").fetchall()
    check("every envelope persisted in order", evs == [(0, "proxy", "step"), (1, "proxy", "blocked"),
                                                       (2, "world", "speech_heard")], str(evs))
    wm = m.walk_memory(0)
    check("walk evidence derived", (5, 4) in wm.tiles and ((5, 4), 0) in wm.blocked, str(wm.stats()))
    ended = m.con.execute("SELECT ended FROM sessions WHERE tag='t1'").fetchone()[0]
    check("session closed", ended is not None)
    check("no writer errors", w.errors == 0, str(w.errors))
    m.close()


def test_ingest():
    print(f"ingest session_{TAG}")
    path = tmpdb()
    out = memory.ingest(path, os.path.join(ROOT, "logs"), [TAG], verbose=False)
    m = Memory(path)
    steps = m.con.execute("SELECT COUNT(*) FROM events WHERE origin='proxy' AND ev='step'").fetchone()[0]
    check("the capture's 36 proxy steps", steps == 36, str(steps))
    check("walk rows derived from them", out["walk_rows"] >= 36, str(out))
    wm = m.walk_memory(0)
    check("the errand's login spot is walked ground", (1963, 2597) in wm.tiles, str(wm.stats()))
    again = memory.ingest(path, os.path.join(ROOT, "logs"), [TAG], verbose=False)
    check("re-ingest of a known tag is a no-op", again["sessions"] == 0 and again["skipped"] == 1, str(again))
    ident = m.con.execute("SELECT char_name, char_serial FROM sessions WHERE tag=?", (TAG,)).fetchone()
    check("ingest records the character", ident == ("TestWorth", 0x00094375), str(ident))
    m.close()


V5_DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE sessions(id INTEGER PRIMARY KEY, tag TEXT UNIQUE NOT NULL, source TEXT NOT NULL,
    started REAL, ended REAL);
CREATE TABLE episodes(id INTEGER PRIMARY KEY, loop TEXT NOT NULL, t_start REAL, t_end REAL, data TEXT NOT NULL);
CREATE TABLE junctures(id INTEGER PRIMARY KEY, t REAL NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL,
    severity TEXT NOT NULL, summary TEXT NOT NULL, data TEXT NOT NULL, acked_t REAL);
CREATE TABLE chat(id INTEGER PRIMARY KEY, t REAL NOT NULL, role TEXT NOT NULL, kind TEXT NOT NULL,
    text TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE job_events(id INTEGER PRIMARY KEY, t REAL NOT NULL, job TEXT NOT NULL, kind TEXT NOT NULL,
    facet INTEGER, x INTEGER, y INTEGER, data TEXT NOT NULL);
INSERT INTO meta VALUES('schema_version', '5');
INSERT INTO sessions(tag, source, started) VALUES('old', 'live', 1.0);
INSERT INTO episodes(loop, t_start, t_end, data) VALUES('lumber', 1.0, 2.0, '{"logs": 3}');
INSERT INTO junctures(t, source, kind, severity, summary, data) VALUES(1.0, 'lumber', 'stuck', 'attention', 's', '{}');
INSERT INTO chat(t, role, kind, text, data) VALUES(1.0, 'user', 'message', 'hi', '{}');
INSERT INTO job_events(t, job, kind, data) VALUES(1.0, 'lumber', 'death', '{}');
"""


def test_migrate_v5():
    print("v5 -> v6 migration")
    import sqlite3
    path = tmpdb()
    con = sqlite3.connect(path)
    con.executescript(V5_DDL)
    con.close()
    m = Memory(path)
    cols = {t: {r[1] for r in m.con.execute(f"PRAGMA table_info({t})")}
            for t in ("sessions", "episodes", "junctures", "chat", "job_events")}
    check("identity columns added to sessions", {"account", "char_serial", "char_name"} <= cols["sessions"],
          str(cols["sessions"]))
    check("char_serial added to the per-character tables",
          all("char_serial" in cols[t] for t in ("episodes", "junctures", "chat", "job_events")), str(cols))
    check("old rows survive, unscoped",
          m.episodes("lumber") == [{"logs": 3}] and [j["char_serial"] for j in m.junctures()] == [None]
          and [c["text"] for c in m.chat()] == ["hi"] and len(m.job_events("lumber")) == 1)
    ver = m.con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    check("schema version 6", ver == "6", ver)
    m.close()
    m = Memory(path)          # reconnecting an already migrated file is a no-op
    check("second connect keeps the rows", len(m.junctures()) == 1)
    m.close()


def test_writer_identity():
    print("MemoryWriter records who logged in")
    path = tmpdb()
    w = MemoryWriter(path)
    w.open_session("t1")
    for seq, data in enumerate(({"ev": "login", "account": "acct1"}, {"ev": "char_select", "name": "Hackworth"},
                                {"ev": "login_confirm", "serial": 0x0020F127})):
        w.record("t1", {"seq": seq, "t": 1.0 + seq, "origin": "world", "data": data}, 0)
    w.flush()
    m = Memory(path)
    row = m.con.execute("SELECT account, char_name, char_serial FROM sessions WHERE tag='t1'").fetchone()
    check("session row has account, name and serial", row == ("acct1", "Hackworth", 0x0020F127), str(row))
    check("name -> serial lookup ignores case", m.char_serial_of("HACKWORTH") == 0x0020F127)
    check("serial -> name lookup", m.char_name_of(0x0020F127) == "Hackworth")
    check("unknown name -> None", m.char_serial_of("Nobody") is None)
    m.close()


def test_scoping():
    print("character scoping")
    path = tmpdb()
    m1, m2, m0 = Memory(path, char_serial=1), Memory(path, char_serial=2), Memory(path)
    m1.juncture("lumber", "stuck", "one")
    m2.juncture("lumber", "stuck", "two")
    m0.juncture("gate", "break_due", "all")
    m1.chat_post("overseer", "from one")
    m2.chat_post("overseer", "from two")
    m0.chat_post("user", "to everyone")
    m1.job_event("gold", "spent", {"gp": 5})
    m2.job_event("gold", "spent", {"gp": 7})
    m1.episode("lumber", {"t_start": 1.0})
    check("scoped juncture reader: own + unscoped",
          [j["summary"] for j in m1.junctures()] == ["one", "all"], str(m1.junctures()))
    check("scoped chat reader: own + unscoped",
          [c["text"] for c in m2.chat()] == ["from two", "to everyone"], str(m2.chat()))
    check("unscoped reader sees every character",
          [j["summary"] for j in m0.junctures()] == ["one", "two", "all"] and len(m0.chat()) == 3)
    check("explicit char_serial overrides the Memory's own",
          [j["summary"] for j in m0.junctures(char_serial=2)] == ["two", "all"])
    check("rows are tagged with the writer's character",
          [j["char_serial"] for j in m0.junctures()] == [1, 2, None])
    check("job_events: every character unless asked",
          len(m1.job_events("gold")) == 2 and [e["data"]["gp"] for e in m0.job_events("gold", char_serial=2)] == [7])
    ep = m0.con.execute("SELECT char_serial FROM episodes").fetchall()
    check("episodes tagged", ep == [(1,)], str(ep))
    m1.juncture("gate", "break_due", "shared", char_serial=None)
    check("a writer can post unscoped", m2.junctures()[-1]["summary"] == "shared")
    for m in (m0, m1, m2):
        m.close()


def test_backfill():
    print("backfill-identity")
    path = tmpdb()
    logdir = tempfile.mkdtemp()
    m = Memory(path)
    c = m.con
    c.execute("INSERT INTO sessions(tag, source) VALUES('ev', 'live')")
    sid = c.execute("SELECT id FROM sessions WHERE tag='ev'").fetchone()[0]
    for seq, d in enumerate(('{"ev": "login", "account": "a1"}', '{"ev": "char_select", "name": "Hackworth"}',
                             '{"ev": "login_confirm", "serial": 2158887}')):
        c.execute("INSERT INTO events VALUES(?,?,?,?,?,?)", (sid, seq, 1.0, "world", d.split('"')[3], d))
    c.execute("INSERT INTO sessions(tag, source) VALUES('epi', 'live')")
    sid = c.execute("SELECT id FROM sessions WHERE tag='epi'").fetchone()[0]
    c.execute("INSERT INTO events VALUES(?,?,?,?,?,?)",
              (sid, 0, 1.0, "world", "char_select", '{"ev": "char_select", "name": "Outland Dan"}'))
    m.episode("lumber", {"character": {"name": "Outland Dan", "serial": "0x0014683F"}})
    c.execute("INSERT INTO sessions(tag, source) VALUES(?, 'live')", (TAG,))   # no events: the raw capture
    c.commit()
    m.close()
    import shutil
    shutil.copy(os.path.join(ROOT, "logs", f"session_{TAG}.s2c.raw"), logdir)
    out = memory.backfill_identity(path, logdir)
    m = Memory(path)
    rows = dict((t, r) for t, *r in m.con.execute("SELECT tag, account, char_name, char_serial FROM sessions"))
    check("from events", rows["ev"] == ["a1", "Hackworth", 0x0020F127], str(rows["ev"]))
    check("serial from the newest episode of the same name", rows["epi"][2] == 0x0014683F, str(rows["epi"]))
    check("serial from the raw capture's login confirm", rows[TAG][2] == 0x00094375, str(rows[TAG]))
    check("counts", out == {"sessions": 3, "from_capture": 1, "from_episodes": 1, "chat": 0, "junctures": 0},
          str(out))
    again = memory.backfill_identity(path, logdir)
    rows2 = dict((t, r) for t, *r in m.con.execute("SELECT tag, account, char_name, char_serial FROM sessions"))
    check("idempotent", rows2 == rows and again["from_episodes"] == 0, str(again))
    m.close()


def test_attribute_rows():
    print("pre-v6 chat/junctures attributed to the character online then")
    path = tmpdb()
    m = Memory(path)
    c = m.con
    # (tag, started, ended, serial); B crashed (no end); C and D overlap; E is the first v6 proxy session
    for tag, started, ended, serial in (("A", 100, 200, 1), ("B", 300, None, 2), ("C", 400, 500, 1),
                                        ("D", 450, 600, 2), ("E", 1000, None, 1)):
        c.execute("INSERT INTO sessions(tag, source, started, ended, char_serial) VALUES(?,?,?,?,?)",
                  (tag, "live", started, ended, serial))
    sid = c.execute("SELECT id FROM sessions WHERE tag='E'").fetchone()[0]
    c.execute("INSERT INTO events VALUES(?,?,?,?,?,?)",
              (sid, 0, 1000.0, "world", "login_confirm", '{"ev": "login_confirm", "serial": 1}'))
    c.commit()
    want = {50: None, 150: 1, 250: 1, 350: 2, 420: 1, 460: None, 700: 2, 1100: None}
    ids = {t: m.chat_post("overseer", f"t{t}", t=t, char_serial=None) for t in want}
    kept = m.chat_post("overseer", "already tagged", t=150, char_serial=2)
    jid = m.juncture("lumber", "stuck", "s", t=350, char_serial=None)
    out = memory.attribute_rows(c)
    got = {t: c.execute("SELECT char_serial FROM chat WHERE id=?", (i,)).fetchone()[0] for t, i in ids.items()}
    check("before any session / two characters online / written by v6: left unscoped; else the newest session's",
          got == want, str(got))
    check("a tagged row is kept", c.execute("SELECT char_serial FROM chat WHERE id=?", (kept,)).fetchone()[0] == 2)
    check("junctures too", c.execute("SELECT char_serial FROM junctures WHERE id=?", (jid,)).fetchone()[0] == 2)
    check("counts", out == {"chat": 5, "junctures": 1}, str(out))
    check("idempotent", memory.attribute_rows(c) == {"chat": 0, "junctures": 0})
    m.close()


def main():
    for t in (test_walk_rows, test_projection, test_harvest, test_writer, test_ingest, test_migrate_v5,
              test_writer_identity, test_scoping, test_backfill, test_attribute_rows):
        t()
    print(f"\nmemory: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

"""discord_kb tests (harness/discord_kb.py, docs/NOTES.md "Discord knowledge base").

  1. Windows are stable: backfilling an earlier day leaves existing window ids alone;
     a truncated reply splits the window in halves that are stored as their own windows.
  2. Grounding: a claim citing an id outside its window, or quoting text its messages
     don't contain, is dropped; authors come from discord.db, not from the model.
  3. Verdict rules: consensus needs >= 2 supporting authors outnumbering the dissent,
     official needs an official claim.
  4. Promotion: re-runs add/update nothing and don't add confirmations; an entry the
     overseer retracted is never re-added; a fact whose verdict drops is retracted only
     when the pipeline added it and nobody confirmed it.
  5. Clustering: removing a cluster's leader claim dissolves the cluster (its fact goes
     'dissolved') and the remaining members re-cluster.

Runs with .venv-discord (numpy). The LLM and the embedder are faked.
"""

import datetime
import json
import os
import sqlite3
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discord_capture as dc  # noqa: E402
import discord_kb as kbm  # noqa: E402
import memory  # noqa: E402
from knowledge import Knowledge  # noqa: E402

FAILURES = []
CH, PN, GUILD = 100, 200, 1


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def sf(day, hh, mm, n=0):
    t = datetime.datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}:00+00:00")
    return ((int(t.timestamp() * 1000) - dc.DISCORD_EPOCH_MS) << 22) + n


def msgs_db(td):
    path = os.path.join(td, "discord.db")
    con = sqlite3.connect(path)
    con.executescript(dc.SCHEMA)
    con.executemany("INSERT INTO channels(id, guild_id, name, type) VALUES (?, ?, ?, 0)",
                    [(CH, GUILD, "harvesting"), (PN, GUILD, "patch-notes")])
    con.commit()
    return path, con


def add_msg(con, mid, content, author_id=7, author="bob", ch=CH):
    ts = datetime.datetime.fromtimestamp(dc.snowflake_ms(mid) / 1000, datetime.timezone.utc).isoformat()
    con.execute("INSERT INTO messages(id, channel_id, guild_id, author_id, author, ts, content) "
                "VALUES (?,?,?,?,?,?,?)", (mid, ch, GUILD, author_id, author, ts, content))
    con.commit()


def test_windows():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:   # open sqlite handles lock files on Windows
        path, con = msgs_db(td)
        add_msg(con, sf("2026-09-10", 10, 0), "cedar needs 60 lumberjacking")
        add_msg(con, sf("2026-09-10", 10, 1), "yes and dullwood needs 65", author_id=8, author="ann")
        add_msg(con, sf("2026-09-10", 12, 0), "a different chat")
        add_msg(con, sf("2026-10-01", 9, 0), "today's message, not processed yet")
        m = kbm.Msgs(path)
        before = [w["id"] for w in kbm.channel_windows(m, CH, "harvesting", "2026-10-01")]
        add_msg(con, sf("2026-09-08", 10, 0), "older backfilled history")
        m = kbm.Msgs(path)
        after = kbm.channel_windows(m, CH, "harvesting", "2026-10-01")
        check(len(before) == 1 and before[0] in [w["id"] for w in after] and len(after) == 2,
              "backfilling an earlier day keeps existing window ids")
        check(all(w["day"] < "2026-10-01" for w in after), "today's messages wait for the day to end")

        kb = kbm.open_kb(os.path.join(td, "kb.db"))
        calls = []

        def fake(prompt, model, thinking, stage, ref=None, validate=None):
            calls.append(ref)
            if "cedar" in prompt and "a different chat" in prompt:   # the whole 09-10 window, not its halves
                raise kbm.Truncated("too long")
            return {"claims": []}, {"model": "fake"}
        kbm.llm_json = fake
        kbm.extract(kb, m, kbm.Budget(1.0), channels=("harvesting",), today="2026-10-01", log=lambda *_: None)
        st = dict(kb.execute("SELECT status, count(*) FROM windows GROUP BY status"))
        check(st == {"ok": 3, "split": 1}, f"a truncated window is split into two stored halves ({st})")
        n = len(calls)
        kbm.extract(kb, m, kbm.Budget(1.0), channels=("harvesting",), today="2026-10-01", log=lambda *_: None)
        check(len(calls) == n, "a second extract makes no calls")
        con.close()


def test_grounding():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        path, con = msgs_db(td)
        a, b = sf("2026-09-10", 10, 0), sf("2026-09-10", 10, 1)
        add_msg(con, a, "Cedar needs **60** lumberjacking\nto chop", author_id=7)
        add_msg(con, b, "and dullwood needs 65", author_id=8)
        w = kbm.channel_windows(kbm.Msgs(path), CH, "harvesting", "2026-10-01")[0]
        good = {"kind": "fact", "topic": "cedar", "statement": "Cedar needs 60 lumberjacking.", "stance": "asserts",
                "uncertain": False, "message_ids": [str(a)], "quote": "cedar needs 60 lumberjacking to chop",
                "entities": ["cedar"]}
        outside = dict(good, message_ids=[str(a), "12345"])
        badquote = dict(good, quote="cedar needs 70 lumberjacking")
        kept, dropped = kbm.check_claims(w, [good, outside, badquote], False)
        check(len(kept) == 1 and dropped == 2, "out-of-window ids and non-substring quotes are dropped")
        check(kept and json.loads(kept[0]["author_ids"]) == [7] and kept[0]["first_ts"].startswith("2026-09-10T10:00"),
              "authors and time come from the cited message")
        con.close()


def test_parse():
    two = '{"facts": [{"cluster_id": 1}]}\n\n{"facts": [{"cluster_id": 2}]}'
    check(kbm.parse_json(two) == {"facts": [{"cluster_id": 1}]}, "a second object after the reply is ignored")
    check(kbm.parse_json('Here it is:\n```json\n{"claims": []}\n```') == {"claims": []}, "prose and fences around it")
    try:
        kbm.parse_json("no json here")
        check(False, "a reply without an object is an error")
    except ValueError:
        check(True, "a reply without an object is an error")


def test_rules():
    check(kbm.apply_rules("consensus", 1, 0, False) == "single_source", "consensus with one author -> single_source")
    check(kbm.apply_rules("official", 1, 0, False) == "single_source", "official without an official claim falls through")
    check(kbm.apply_rules("official", 0, 3, True) == "official", "official with an official claim stays")
    check(kbm.apply_rules("consensus", 2, 2, False) == "disputed", "2 supporters vs 2 contradictors -> disputed")
    check(kbm.apply_rules("consensus", 3, 1, False) == "consensus", "3 vs 1 stays consensus")
    check(kbm.apply_rules("single_source", 1, 1, False) == "disputed", "contradicted single source -> disputed")
    check(kbm.confidence("consensus", 2) == 0.65 and kbm.confidence("consensus", 9) == 0.80, "consensus confidence")


def _fact(kb, cluster_id, statement, verdict="consensus", conf=0.65, imp=9):
    kb.execute("INSERT INTO facts(cluster_id, kind, topic, statement, section, verdict, confidence, importance, "
               "support_authors, contradict_authors, tags, entities, evidence) VALUES (?, 'fact', ?, ?, 'other', ?, ?, "
               "?, 2, 0, '[\"wood\"]', '[]', '[]')", (cluster_id, f"topic {cluster_id}", statement, verdict, conf, imp))
    kb.commit()
    return kb.execute("SELECT id FROM facts WHERE cluster_id=?", (cluster_id,)).fetchone()[0]


def _f(kb, fid, col):
    """A promotion field of the fact (each test promotes into one store), or a fact field."""
    if col in ("knowledge_id", "knowledge_action", "promoted_hash", "knowledge_status"):
        r = kb.execute(f"SELECT {col} FROM promotions WHERE fact_id=?", (fid,)).fetchone()
        return r[0] if r else None
    return kb.execute(f"SELECT {col} FROM facts WHERE id=?", (fid,)).fetchone()[0]


def test_promote():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        path, con = msgs_db(td)
        m, hdb = kbm.Msgs(path), os.path.join(td, "harness.db")
        kb = kbm.open_kb(os.path.join(td, "kb.db"))
        quiet = lambda *_: None  # noqa: E731
        f1 = _fact(kb, 1, "Cedar trees need 60 lumberjacking to chop")
        f2 = _fact(kb, 2, "Dullwood logs weigh two stones each in Outlands", verdict="official", conf=0.85)
        f3 = _fact(kb, 3, "Shadowwood trees appear near Prevalia only", verdict="single_source", conf=0.5)
        r = kbm.promote(kb, m, hdb, log=quiet)
        k = Knowledge(memory.connect(hdb))
        e1, e2 = k.get(_f(kb, f1, "knowledge_id")), k.get(_f(kb, f2, "knowledge_id"))
        check(r.get("added") == 2 and _f(kb, f3, "knowledge_id") is None, f"official+consensus promoted only ({r})")
        check((e1["source_type"], e1["importance"], e2["source_type"], e2["confidence"]) == ("community", 6, "doc", 0.85)
              and "discord" in e1["tags"] and e1["source_ref"].startswith("discord-kb:"),
              "source/importance cap/tags/ref of promoted entries")
        r = kbm.promote(kb, m, hdb, log=quiet)
        check(not r.get("add") and not r.get("update") and k.get(e1["id"])["confirmations"] == 0,
              f"second promote: no adds, no updates, no confirmations ({r})")
        other = os.path.join(td, "copy.db")
        r = kbm.promote(kb, m, other, log=quiet)
        check(r.get("added") == 2, f"a trial promote into another store starts from scratch ({r})")
        kb.execute("DELETE FROM promotions WHERE target=?", (os.path.normcase(os.path.abspath(other)),))
        kb.commit()

        # the overseer retracts #1; the statement changes; promote must not bring it back
        k.retract(e1["id"], "wrong: cedar needs 70")
        kb.execute("UPDATE facts SET statement='Cedar trees need 65 lumberjacking to chop' WHERE id=?", (f1,))
        kb.commit()
        n_active = k.con.execute("SELECT count(*) FROM knowledge WHERE status='active'").fetchone()[0]
        kbm.promote(kb, m, hdb, log=quiet)
        check(k.con.execute("SELECT count(*) FROM knowledge WHERE status='active'").fetchone()[0] == n_active
              and _f(kb, f1, "knowledge_status") == "retracted", "an overseer-retracted entry is never re-added")

        # verdict drops: our unconfirmed entry is retracted; a confirmed one and an entry we only confirmed stay
        f4 = _fact(kb, 4, "Goldenwood trees need 85 lumberjacking to chop")
        f5 = _fact(kb, 5, "Hatchets are sold by the provisioner in Prevalia")
        pre = k.add("fact", "avarwood", "Avarwood needs 110 lumberjacking", source="observed")["id"]
        f6 = _fact(kb, 6, "Avarwood needs 110 lumberjacking")
        kbm.promote(kb, m, hdb, log=quiet)
        check(_f(kb, f6, "knowledge_action") == "confirmed" and _f(kb, f6, "knowledge_id") == pre,
              "a fact matching an existing entry confirms it")
        k.confirm(_f(kb, f5, "knowledge_id"), source="observed", ref="chat#1")
        kb.execute("UPDATE facts SET verdict='disputed', confidence=0.35 WHERE id IN (?, ?, ?, ?)", (f2, f4, f5, f6))
        kb.commit()
        r = kbm.promote(kb, m, hdb, log=quiet)
        check(k.get(_f(kb, f4, "knowledge_id"))["status"] == "retracted"
              and k.get(_f(kb, f2, "knowledge_id"))["status"] == "retracted", f"unconfirmed pipeline entries retracted ({r})")
        check(k.get(_f(kb, f5, "knowledge_id"))["status"] == "active", "an overseer-confirmed entry is left alone")
        check(k.get(pre)["status"] == "active", "an entry the pipeline only confirmed is left alone")

        # back to consensus after our own retraction: re-added
        kb.execute("UPDATE facts SET verdict='consensus', confidence=0.65 WHERE id=?", (f4,))
        kb.commit()
        kbm.promote(kb, m, hdb, log=quiet)
        check(k.get(_f(kb, f4, "knowledge_id"))["status"] == "active", "a fact the pipeline retracted comes back")
        k.con.close()
        con.close()


def fake_embed(texts):
    axes = ("cedar", "dullwood", "goldenwood")
    out = []
    for t in texts:
        v = np.array([1.0 if a in t.lower() else 0.0 for a in axes] + [0.01], dtype=np.float32)
        if "bark" in t.lower():
            v[3] = 0.5    # a cedar claim that is close but not identical
        out.append(v / np.linalg.norm(v))
    return np.vstack(out)


def test_clusters():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        kb = kbm.open_kb(os.path.join(td, "kb.db"))
        kbm.embed = fake_embed

        def claim(statement, kind="fact"):
            kb.execute("INSERT INTO claims(window_id, kind, topic, statement, message_ids, author_ids, quote, "
                       "first_ts, official, stance, uncertain, entities) VALUES ('w', ?, 't', ?, '[1]', '[7]', 'q', "
                       "'2026-09-10', 0, 'asserts', 0, '[]')", (kind, statement))
            return kb.execute("SELECT max(id) FROM claims").fetchone()[0]
        lead = claim("cedar needs 60")
        c2, c3 = claim("cedar needs sixty"), claim("cedar bark is thick")
        o = claim("dullwood needs 65")
        claim("cedar chopping steps", kind="procedure")
        quiet = lambda *_: None  # noqa: E731
        kbm.cluster(kb, log=quiet)
        cid = kb.execute("SELECT cluster_id FROM claims WHERE id=?", (lead,)).fetchone()[0]
        members = sorted(r[0] for r in kb.execute("SELECT id FROM claims WHERE cluster_id=?", (cid,)))
        check(members == [lead, c2, c3], f"similar claims of one kind share a cluster ({members})")
        check(kb.execute("SELECT count(*) FROM clusters").fetchone()[0] == 3, "other topics and kinds get their own")
        kb.execute("INSERT INTO facts(cluster_id, verdict, statement, topic) VALUES (?, 'consensus', 's', 't')", (cid,))
        kb.execute("DELETE FROM claims WHERE id=?", (lead,))   # its window was dropped
        kb.commit()
        kbm.cluster(kb, log=quiet)
        check(kb.execute("SELECT count(*) FROM clusters WHERE id=?", (cid,)).fetchone()[0] == 0
              and kb.execute("SELECT verdict FROM facts WHERE cluster_id=?", (cid,)).fetchone()[0] == "dissolved",
              "leader removal dissolves the cluster and its fact")
        new = {r[0] for r in kb.execute("SELECT cluster_id FROM claims WHERE id IN (?, ?)", (c2, c3))}
        check(len(new) == 1 and None not in new and cid not in new, "the remaining members re-cluster together")
        check(kb.execute("SELECT cluster_id FROM claims WHERE id=?", (o,)).fetchone()[0] is not None,
              "untouched clusters keep their members")


def main():
    for t in (test_windows, test_grounding, test_parse, test_rules, test_promote, test_clusters):
        print(f"== {t.__name__} ==")
        t()
    print(f"\ndiscord_kb: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

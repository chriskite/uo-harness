"""forum_kb tests (harness/forum_kb.py: the Discord KB pipeline over the Outlands forums).

  1. Thread windows are stable: appending a reply leaves every earlier part's id alone;
     today's posts wait, empty posts are skipped, an over-long post is read whole in pieces;
     a truncated reply splits a window into halves that keep the thread title.
  2. Grounding: a quote of text that only appears inside a quoted earlier post is dropped.
  3. Official: a staff post in Announcements gives an official claim; a staff post in General
     Discussion or a player's post in Announcements doesn't.
  4. Promotion: entries are tagged `forum` with a `forum-kb:` ref carrying the date span;
     facts last seen before PROMOTE_SINCE are not promoted, and are retracted when they go
     stale after promotion; entries the Discord corpus promoted are never touched.
  5. Adjudication skips one-author clusters without a staff claim (they can't be promoted);
     one that drops to a single author loses its fact. Discord still adjudicates all.

Runs with plain python (numpy). The LLM and the embedder are faked.
"""

import datetime
import json
import os
import re
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discord_capture as dc  # noqa: E402
import discord_kb as kbm  # noqa: E402
import forum_kb as fkb  # noqa: E402
import memory  # noqa: E402
import outlands_web as ow  # noqa: E402
from knowledge import Knowledge  # noqa: E402

FAILURES = []
TODAY = "2026-10-01"
GENERAL, ANNOUNCEMENTS = 3, 6
QUIET = lambda *_: None  # noqa: E731
QUOTE = ('<blockquote data-attributes="member: 8" data-quote="ann" class="bbCodeBlock bbCodeBlock--expandable '
         'bbCodeBlock--quote js-expandWatch"><div class="bbCodeBlock-content"><div class="bbCodeBlock-expandContent '
         'js-expandContent">{}</div></div></blockquote>')


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def ts(day, hh=12):
    return int(datetime.datetime.fromisoformat(f"{day}T{hh:02d}:00:00+00:00").timestamp())


def web_db(td):
    path = os.path.join(td, "outlands_web.db")
    return path, ow.connect(path)


def add_thread(con, tid, node, title):
    con.execute("INSERT INTO threads(id, node_id, title, url, author, started_ts, last_ts, replies) "
                "VALUES (?,?,?,?,?,?,?,0)", (tid, node, title, f"{ow.FORUM}/index.php?threads/x.{tid}/", "ann", 0, 0))
    con.commit()


def add_post(con, pid, tid, node, pos, html, day, author="bob", author_id=7, staff=0):
    con.execute("INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, tid, node, pos, author, author_id, staff, ts(day), html))
    con.commit()


def test_windows():
    saved = kbm.WINDOW_CHARS
    kbm.WINDOW_CHARS = 400
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:   # open sqlite handles lock files on Windows
            path, con = web_db(td)
            add_thread(con, 10, GENERAL, "Cedar chat")
            for i in range(5):
                add_post(con, 100 + i, 10, GENERAL, i, f"<p>post {i}: " + "cedar needs sixty lumberjacking " * 4 + "</p>",
                         f"2024-03-0{i + 1}", author_id=7 + i % 2)
            add_post(con, 105, 10, GENERAL, 5, QUOTE.format("only a quote"), "2024-03-06")
            add_post(con, 106, 10, GENERAL, 6, "<p>today's reply</p>", TODAY)
            f = fkb.Forum(path)
            before, nodes = f.windows(["3"], TODAY)
            ids = [r[0] for w in before for u in w["units"] for _, r in u]
            check(nodes == [GENERAL] and len(before) >= 2, f"a long thread packs into several parts ({len(before)})")
            check(105 not in ids and 106 not in ids, "posts with no text of their own and today's posts are skipped")
            check(all(w["title"] == "Cedar chat" and w["part"].startswith("10.") for w in before)
                  and before[0]["day"] == "2024-03-01", "a window carries the thread title, its part the thread id")
            con.execute("DELETE FROM posts WHERE id=106")
            add_post(con, 107, 10, GENERAL, 6, "<p>a later reply: oak needs thirty</p>", "2024-04-01")
            after, _ = fkb.Forum(path).windows(["General Discussion"], TODAY)
            check([w["id"] for w in after[:len(before) - 1]] == [w["id"] for w in before[:-1]],
                  "appending a reply keeps every earlier part's id")
            check(after[-1]["last_id"] == 107 and after[-1]["id"] != before[-1]["id"], "the last part takes the reply")

            long_html = ("<p>Patch intro line.</p><p>" + "guide words here " * 40 + "</p><p>" + "x" * 300
                         + "</p><p>" + "more notes " * 30 + "</p><p>final marker: ash needs ninety</p>")
            add_post(con, 108, 10, GENERAL, 7, long_html, "2024-04-02")
            f2 = fkb.Forum(path)
            ws = f2.windows([GENERAL], TODAY)[0]
            head = "[108] 2024-04-02 bob: "
            pieces = [line for w in ws for u in w["units"] for line, r in u if r[0] == 108]
            text = next(r[4] for w in ws for u in w["units"] for _, r in u if r[0] == 108)
            prompts = "\n".join(f2.extract_prompt(w) for w in ws)
            check(len(pieces) >= 3 and all(p.startswith(head) and len(p) <= kbm.WINDOW_CHARS // 2 for p in pieces)
                  and all(p in prompts for p in pieces), f"an over-long post becomes prefixed pieces ({len(pieces)})")
            check(re.sub(r"[\s/]", "", "".join(p[len(head):] for p in pieces)) == re.sub(r"[\s/]", "", text),
                  "every character of an over-long post reaches a window's prompt")
            check([w["id"] for w in ws] == [w["id"] for w in fkb.Forum(path).windows([GENERAL], TODAY)[0]],
                  "window ids stay deterministic")
            last = next(w for w in ws if any(line == pieces[-1] for u in w["units"] for line, _ in u))
            claim = {"kind": "fact", "topic": "ash", "statement": "Ash needs ninety lumberjacking.", "stance": "asserts",
                     "uncertain": False, "message_ids": ["108"], "quote": "ash needs ninety", "entities": ["ash"]}
            kept, dropped = kbm.check_claims(last, [claim], f2.is_official)
            check(len(kept) == 1 and dropped == 0, "a claim quoting the last piece of a long post is kept")

            kb = kbm.open_kb(os.path.join(td, "kb.db"))
            calls, prompts = [], []

            def fake(prompt, model, thinking, stage, ref=None, validate=None):
                calls.append(ref)
                prompts.append(prompt)
                if prompt.count("\n[") >= 2:   # any multi-post window
                    raise kbm.Truncated("too long")
                return {"claims": []}, {"model": "fake"}
            kbm.llm_json = fake
            kbm.extract(kb, fkb.Forum(path), kbm.Budget(1.0), names=("3",), today=TODAY, log=QUIET)
            st = dict(kb.execute("SELECT status, count(*) FROM windows GROUP BY status"))
            check(st.get("split", 0) >= 1 and st.get("ok", 0) >= 2 and "failed" not in st,
                  f"a truncated window is split into stored halves ({st})")
            check(all('section "General Discussion", thread "Cedar chat"' in p for p in prompts),
                  "every prompt, halves included, names the section and the thread title")
            n = len(calls)
            kbm.extract(kb, fkb.Forum(path), kbm.Budget(1.0), names=("3",), today=TODAY, log=QUIET)
            check(len(calls) == n, "a second extract makes no calls")
            con.close()
    finally:
        kbm.WINDOW_CHARS = saved


def test_grounding():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        path, con = web_db(td)
        add_thread(con, 20, GENERAL, "Wood")
        add_post(con, 200, 20, GENERAL, 0, "<p>Cedar needs 60 lumberjacking to chop.</p>", "2024-05-01",
                 author="ann", author_id=8)
        add_post(con, 201, 20, GENERAL, 1, QUOTE.format("Cedar needs 60 lumberjacking to chop.")
                 + "<p>Agreed, and oak needs 30.</p>", "2024-05-02", author="bob", author_id=7)
        f = fkb.Forum(path)
        w = f.windows([GENERAL], TODAY)[0][0]
        base = {"kind": "fact", "topic": "cedar", "statement": "Cedar needs 60 lumberjacking.", "stance": "asserts",
                "uncertain": False, "entities": ["cedar"]}
        quoted = dict(base, message_ids=["201"], quote="Cedar needs 60 lumberjacking to chop")
        own = dict(base, message_ids=["200"], quote="Cedar needs 60 lumberjacking to chop")
        reply = dict(base, topic="oak", statement="Oak needs 30 lumberjacking.", message_ids=["201"],
                     quote="oak needs 30")
        kept, dropped = kbm.check_claims(w, [quoted, own, reply], f.is_official)
        check(dropped == 1 and [json.loads(c["message_ids"]) for c in kept] == [[200], [201]],
              "a quote found only inside a quoted earlier post is dropped")
        check(json.loads(kept[0]["author_ids"]) == [8] and json.loads(kept[1]["author_ids"]) == [7]
              and kept[0]["first_ts"].startswith("2024-05-01"), "authors and time come from the cited post")
        con.close()


def test_official():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        path, con = web_db(td)
        add_thread(con, 30, ANNOUNCEMENTS, "Patch Notes for May 1, 2024")
        add_post(con, 300, 30, ANNOUNCEMENTS, 0, "<p>Cedar now needs 65 lumberjacking.</p>", "2024-05-01",
                 author="Luthius", author_id=6, staff=1)
        add_post(con, 301, 30, ANNOUNCEMENTS, 1, "<p>Oak still needs 30 lumberjacking.</p>", "2024-05-02")
        add_thread(con, 31, GENERAL, "Yew")
        add_post(con, 310, 31, GENERAL, 0, "<p>Yew needs 80 lumberjacking.</p>", "2024-05-03",
                 author="Owyn", author_id=1, staff=1)
        kb = kbm.open_kb(os.path.join(td, "kb.db"))
        prompts = []

        def fake(prompt, model, thinking, stage, ref=None, validate=None):
            prompts.append(prompt)
            claims = [{"kind": "fact", "topic": "wood", "statement": text, "stance": "asserts", "uncertain": False,
                       "message_ids": [mid], "quote": text, "entities": []}
                      for mid, text in re.findall(r"^\[(\d+)\] \S+ [^:]+: (.+)$", prompt, re.M)]
            return {"claims": claims}, {"model": "fake"}
        kbm.llm_json = fake
        kbm.extract(kb, fkb.Forum(path), kbm.Budget(1.0), names=("6", "3"), today=TODAY, log=QUIET)
        off = {json.loads(m)[0]: o for m, o in kb.execute("SELECT message_ids, official FROM claims")}
        check(off == {300: 1, 301: 0, 310: 0},
              f"official = staff post in an official section; not staff elsewhere, not players there ({off})")
        check(any("Luthius (staff): Cedar" in p for p in prompts), "staff authors are marked in the prompt")
        con.close()


def _fact(kb, cluster_id, statement, first, last, verdict="consensus", conf=0.65, evidence=(555,)):
    kb.execute("INSERT INTO facts(cluster_id, kind, topic, statement, section, verdict, confidence, importance, "
               "support_authors, contradict_authors, first_seen, last_seen, tags, entities, evidence) "
               "VALUES (?, 'fact', ?, ?, 'other', ?, ?, 9, 2, 0, ?, ?, '[\"wood\"]', '[]', ?)",
               (cluster_id, f"topic {cluster_id}", statement, verdict, conf, first, last, json.dumps(list(evidence))))
    kb.commit()
    return kb.execute("SELECT id FROM facts WHERE cluster_id=?", (cluster_id,)).fetchone()[0]


def _kid(kb, fid):
    r = kb.execute("SELECT knowledge_id FROM promotions WHERE fact_id=?", (fid,)).fetchone()
    return r[0] if r else None


def test_promote():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        hdb = os.path.join(td, "harness.db")
        dpath = os.path.join(td, "discord.db")
        dcon = sqlite3.connect(dpath)
        dcon.executescript(dc.SCHEMA)
        dcon.close()
        dkb = kbm.open_kb(os.path.join(td, "discord_kb.db"))
        _fact(dkb, 1, "Hatchets are sold by the provisioner in Prevalia", "2025-01-01", "2025-02-01", evidence=())
        kbm.promote(dkb, kbm.Msgs(dpath), hdb, log=QUIET)
        k = Knowledge(memory.connect(hdb))
        d_id = _kid(dkb, 1)
        d_row = k.con.execute("SELECT * FROM knowledge WHERE id=?", (d_id,)).fetchone()

        path, con = web_db(td)
        f = fkb.Forum(path)
        kb = kbm.open_kb(os.path.join(td, "forum_kb.db"))
        f1 = _fact(kb, 1, "Cedar trees need 60 lumberjacking to chop", "2023-05-01", "2024-02-01")
        f2 = _fact(kb, 2, "Dullwood logs weigh two stones each", "2024-01-01", "2025-03-01", verdict="official",
                   conf=0.85)
        f3 = _fact(kb, 3, "Ash trees only grow near Prevalia", "2019-01-01", "2022-06-01")
        f4 = _fact(kb, 4, "Yew trees need 80 lumberjacking to chop", "2021-01-01", fkb.PROMOTE_SINCE)
        r = kbm.promote(kb, f, hdb, log=QUIET)
        e1, e2 = k.get(_kid(kb, f1)), k.get(_kid(kb, f2))
        check(r.get("added") == 3 and r.get("stale") == 1 and _kid(kb, f3) is None and _kid(kb, f4) is not None,
              f"facts last seen before {fkb.PROMOTE_SINCE} are not promoted ({r})")
        check(e1["source_ref"] == f"forum-kb:{f1} {ow.FORUM}/index.php?posts/555/ (2023-05-01..2024-02-01)",
              f"the ref carries the fact id, post link and date span ({e1['source_ref']})")
        check("forum" in e1["tags"] and "discord" not in e1["tags"]
              and (e1["source_type"], e1["importance"], e2["source_type"]) == ("community", 6, "doc"),
              "tag forum, source community/doc, importance capped")
        r = kbm.promote(kb, f, hdb, log=QUIET)
        check(not r.get("add") and not r.get("update") and r.get("stale") == 1, f"second promote changes nothing ({r})")

        # f1 goes stale (its recent claims were dropped), f2's verdict drops: both retracted with our prefix
        kb.execute("UPDATE facts SET last_seen='2022-11-30' WHERE id=?", (f1,))
        kb.execute("UPDATE facts SET verdict='disputed', confidence=0.35 WHERE id=?", (f2,))
        kb.commit()
        r = kbm.promote(kb, f, hdb, log=QUIET)
        e1, e2 = k.get(e1["id"]), k.get(e2["id"])
        check(r.get("retract") == 2 and e1["status"] == "retracted" and e2["status"] == "retracted"
              and e1["retract_reason"].startswith("forum-kb:") and "2022-11-30" in e1["retract_reason"],
              f"a promoted fact that goes stale is retracted like a dropped verdict ({r})")
        check(k.con.execute("SELECT * FROM knowledge WHERE id=?", (d_id,)).fetchone() == d_row,
              "the entry the Discord corpus promoted is untouched")
        check(d_id not in {r[0] for r in kb.execute("SELECT knowledge_id FROM promotions")},
              "forum promotions never record a Discord entry")

        out = kbm.digest(kb, f, os.path.join(td, "FORUM_KB.md"), log=QUIET)
        with open(out, encoding="utf-8") as fh:
            text = fh.read()
        check(text.startswith("# Outlands forum knowledge base") and "Ash trees only grow near Prevalia" in text
              and f"{ow.FORUM}/index.php?posts/555/" in text, "stale facts stay in the digest, with post links")
        hits = kbm.search(kb, f, "yew lumberjacking")
        check(hits and hits[0]["link"] == f"{ow.FORUM}/index.php?posts/555/", "search links forum posts")
        k.con.close()
        con.close()


def test_adjudicate_min_authors():
    """Forum clusters with one author and no staff claim are never sent to the LLM; a cluster
    that drops to one author loses its fact."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        kb = kbm.open_kb(os.path.join(td, "kb.db"))
        rows = [(1, 1, [7], 0), (2, 1, [8], 0),        # cluster 1: two players
                (3, 2, [7], 0), (4, 2, [7], 0),        # cluster 2: one player twice
                (5, 3, [9], 1)]                        # cluster 3: one staff claim
        for cid in (1, 2, 3):
            kb.execute("INSERT INTO clusters(id, kind, leader_claim_id, signature) VALUES (?, 'fact', ?, ?)",
                       (cid, cid, f"s{cid}"))
        for claim, cid, authors, off in rows:
            kb.execute("INSERT INTO claims(id, window_id, kind, topic, statement, entities, stance, uncertain, "
                       "message_ids, author_ids, quote, first_ts, official, cluster_id) VALUES "
                       "(?, 'w', 'fact', 't', 's', '[]', 'asserts', 0, ?, ?, 'q', '2024-01-01', ?, ?)",
                       (claim, json.dumps([claim]), json.dumps(authors), off, cid))
        kb.commit()
        sent = []

        def fake(prompt, model, thinking, stage, ref=None, validate=None):
            ids = [int(x) for x in re.findall(r"^Cluster (\d+) ", prompt, re.M)]
            sent.extend(ids)
            out = {"facts": [{"cluster_id": c, "kind": "fact", "topic": "t", "statement": "s", "verdict": "consensus",
                              "importance": 3, "section": "other", "supporting_claim_ids": [], "tags": []}
                             for c in ids]}
            validate(out)
            return out, {"model": "fake"}
        kbm.llm_json = fake
        kbm.adjudicate(kb, kbm.Budget(1.0), fkb.Forum.adjudicate_min_authors, log=QUIET)
        check(sorted(sent) == [1, 3], f"only multi-author or staff clusters are adjudicated ({sent})")
        kb.execute("DELETE FROM claims WHERE id=2")
        kb.execute("UPDATE clusters SET signature='s1b' WHERE id=1")
        kb.commit()
        sent.clear()
        kbm.adjudicate(kb, kbm.Budget(1.0), fkb.Forum.adjudicate_min_authors, log=QUIET)
        v = kb.execute("SELECT verdict FROM facts WHERE cluster_id=1").fetchone()[0]
        check(sent == [] and v == "skipped", f"a cluster down to one author loses its fact ({sent}, {v})")
        check(kbm.Msgs.adjudicate_min_authors == 1, "the Discord corpus still adjudicates every cluster")


def main():
    for t in (test_windows, test_grounding, test_official, test_promote, test_adjudicate_min_authors):
        print(f"== {t.__name__} ==")
        t()
    print(f"\nforum_kb: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

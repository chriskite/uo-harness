"""Wiki/news knowledge sync and the site parsers (harness/web_kb.py, harness/outlands_web.py;
docs/NOTES.md "Outlands wiki, news and forums").

  1. HTML -> text: headings, lists and tables survive (a header row labels every cell), edit
     links / scripts / navboxes don't; a forum post's quoted earlier post is dropped when asked.
  2. Forum pages: staff is the "Staff Member" banner or a staff/admin/moderator name style
     (developers carry only the banner); the post body is the bbWrapper's content; thread
     listings give id, last-post time and reply count.
  3. Chunks: one per heading path (a short line before a list is a heading, as news posts
     write them), at most CHUNK_CHARS, keys stable when another section changes, topics
     carry the page (wiki) or the date and title (news), duplicate boilerplate kept once.
  4. Sync: adds wiki (source wiki) and news (source doc) entries once; a re-sync changes
     nothing and confirms nothing; an edited section supersedes its entry; a removed section
     is retracted unless someone confirmed it; an entry the overseer retracted stays out; lost
     sync state adopts the entries by their ref instead of adding or confirming them again.
  5. A forum crawl that was interrupted after listing still fetches every listed thread.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import memory  # noqa: E402
import outlands_web as ow  # noqa: E402
import web_kb as wk  # noqa: E402
from knowledge import Knowledge  # noqa: E402

FAILURES = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


WIKI_HTML = """<div class="mw-parser-output"><p>Players can use a <a href="/Hatchet">Hatchet</a> to chop trees.</p>
<div id="toc" class="toc"><ul><li>1 Summary</li></ul></div>
<h2><span class="mw-headline" id="Colored_Hatchets">Colored Hatchets</span><span class="mw-editsection">[edit]</span></h2>
<p>Bonuses stack:</p>
<table class="wikitable"><tbody><tr><th>Material</th><th>Tool Bonus</th></tr>
<tr><td>Iron</td><td>0</td></tr><tr><td>Bronze</td><td>0.08</td></tr></tbody></table>
<h3><span class="mw-headline" id="Uses">Uses</span></h3><ul><li>Iron: 500 base</li><li>Exceptional: +250</li></ul>
<script>var x = 1;</script><table class="navbox"><tr><td>Skills nav</td></tr></table>
<table class="infobox"><tr><th>Skill</th><td>Lumberjacking</td></tr></table></div>"""

NEWS_HTML = """<p><b>Storage Shelves</b></p><ul><li>Loadouts can be renamed</li><li>Loadouts switch with arrows</li></ul>
<p><b>Provocation</b></p><ul><li>The maximum range is now 40 tiles</li></ul>"""


def post_html(pid, author, banner=False, name_cls="", body="text"):
    ban = ('<div class="userBanner userBanner--staff message-userBanner" itemprop="jobTitle">'
           '<span class="userBanner-before"></span><strong>Staff Member</strong></div>') if banner else ""
    return (f'<article class="message message--post js-post" data-author="{author}" data-content="post-{pid}">'
            f'<h4 class="message-name"><a href="/index.php?members/x.{pid}/" class="username" data-user-id="{pid + 1000}">'
            f'<span class="{name_cls}">{author}</span></a></h4>{ban}'
            f'<div class="message-attribution"><time class="u-dt" data-timestamp="{1788537358 + pid}"></time></div>'
            f'<article class="message-body"><div itemprop="text"><div class="bbWrapper">{body}</div></div>'
            f'<div class="js-selectToQuoteEnd">&nbsp;</div></article></article>')


def test_text():
    t = ow.to_text(WIKI_HTML)
    check("Bronze" in t and "Material: Bronze; Tool Bonus: 0.08" in t, "a header row labels table cells")
    check("Skill: Lumberjacking" in t, "an infobox th/td pair reads 'label: value'")
    check("[edit]" not in t and "var x" not in t and "Skills nav" not in t and "1 Summary" not in t,
          "edit links, scripts, navboxes and the TOC are dropped")
    check("## Colored Hatchets" in t and "- Iron: 500 base" in t, "headings and list items survive")
    quoted = ('<blockquote class="bbCodeBlock bbCodeBlock--expandable bbCodeBlock--quote" data-source="post: 1">'
              '<div class="bbCodeBlock-content">Cedar needs 60 skill.</div></blockquote>No, it needs 70.')
    check("Cedar" in ow.to_text(quoted) and ow.to_text(quoted, strip_quotes=True) == "No, it needs 70.",
          "a forum quote is dropped only when asked")


def test_forum_pages():
    page = (post_html(1, "Luthius", banner=True, name_cls="username--style3 username--moderator username--admin",
                      body="<b>Patch</b><br />Shelves got loadouts.")
            + post_html(2, "Owyn", name_cls="username--staff username--admin")
            + post_html(3, "a Tea tree", body="Thanks! Staff Member of the year")
            + '<div class="block-outer"><a href="/index.php?threads/patch.6336/page-3">3</a></div>')
    ps = ow.parse_posts(page)
    check([(p["id"], p["staff"]) for p in ps] == [(1, 1), (2, 1), (3, 0)],
          "staff: banner-only developer and staff name style yes, a player quoting 'Staff Member' no")
    check(ps[0]["html"] == "<b>Patch</b><br />Shelves got loadouts." and ps[0]["author_id"] == 1001
          and ps[0]["ts"] == 1788537359, "body, author id and time come from the post")
    check(ow.last_page(page, r"threads/[^\"/]*?\.6336/") == 3, "the thread's last page from its page links")
    listing = """<div class="structItem structItem--thread js-threadListItem-4000" data-author="clx">
      <div class="structItem-title"><a href="/index.php?threads/exitlag-ping.4000/" data-tp-primary="on">ExitLag &#039;Ping&#039;</a></div>
      <time class="u-dt" data-timestamp="1643646146"></time><dl class="pairs"><dt>Replies</dt><dd>1,204</dd></dl>
      <time class="structItem-latestDate u-dt" data-timestamp="1760190381"></time></div>
      <div class="block-footer"></div>"""
    t = ow.parse_thread_list(listing)
    check(t == [{"id": 4000, "url": ow.FORUM + "/index.php?threads/exitlag-ping.4000/", "title": "ExitLag 'Ping'",
                 "author": "clx", "started_ts": 1643646146, "last_ts": 1760190381, "replies": 1204}],
          f"a listing row: id, title, start, last post, replies ({t})")


def web_db(td, wiki=WIKI_HTML, news=NEWS_HTML):
    con = ow.connect(os.path.join(td, "web.db"))
    con.execute("DELETE FROM docs")
    con.executemany("INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?,?)", [
        ("wiki:7", "wiki", "wiki", "Lumberjacking", "https://wiki.uooutlands.com/Lumberjacking", "1", "t1", None,
         wiki, 0),
        ("wiki:8", "wiki", "wiki", "Mining", "https://wiki.uooutlands.com/Mining", "1", "t1", None,
         "<p>Players can use a <a>Hatchet</a> to chop trees.</p><h2>Ore</h2><p>Iron ore is common.</p>", 0),
        ("news:5", "news", "patch", "PATCH: QoL", "https://uooutlands.com/news/qol/", None, "m1",
         "2026-09-04T15:00:00", news, 0),
        ("news:6", "news", "video", "VIDEO: Shard Happens", "https://uooutlands.com/news/v/", None, "m1",
         "2026-09-29T15:00:00", "<p>Watch it</p>", 0)])
    con.commit()
    return con


def test_chunks():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        web = web_db(td)
        cs = wk.all_chunks(web)
        by = {c["key"]: c for c in cs}
        check(set(by) == {"wiki:7/_/0", "wiki:7/colored_hatchets/0", "wiki:7/colored_hatchets/uses/0",
                          "wiki:8/ore/0", "news:5/storage_shelves/0", "news:5/provocation/0"},
              f"one chunk per heading path; news bold lines before lists are headings; video skipped "
              f"({sorted(by)})")
        check(not any(k.startswith("wiki:8/_") for k in by), "the same boilerplate text on another page is kept once")
        c = by["wiki:7/colored_hatchets/0"]
        check(c["topic"] == "Lumberjacking > Colored Hatchets" and c["source"] == "wiki" and c["tags"] == ["wiki"]
              and c["ref"] == "web-kb:wiki:7/colored_hatchets/0 https://wiki.uooutlands.com/Lumberjacking#Colored_Hatchets",
              f"wiki chunk topic, source and section link ({c['topic']}, {c['ref']})")
        n = by["news:5/provocation/0"]
        check(n["topic"] == "2026-09-04 PATCH: QoL > Provocation" and n["source"] == "doc"
              and n["tags"] == ["news", "patch"] and n["content"] == "- The maximum range is now 40 tiles",
              f"news chunk carries date and title ({n['topic']})")
        long = "<h2>Big</h2>" + "".join(f"<p>Sentence number {i} explains a rule in some detail.</p>" for i in range(80))
        parts = wk.doc_chunks("wiki:9", "wiki", "wiki", "Big", "u", None, long)
        check(len(parts) > 2 and all(len(p["content"]) <= wk.CHUNK_CHARS for p in parts)
              and parts[1]["topic"].startswith("Big > Big (2/"), "long sections split under CHUNK_CHARS, numbered")
        edited = WIKI_HTML.replace("Players can use", "Anyone can use")
        keys2 = {c["key"] for c in wk.doc_chunks("wiki:7", "wiki", "wiki", "Lumberjacking", "u", None, edited)}
        check({k for k in by if k.startswith("wiki:7")} == keys2, "editing one section keeps every chunk key")
        web.close()


def test_sync():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        web = web_db(td)
        hdb = os.path.join(td, "harness.db")
        memory.connect(hdb).close()
        quiet = lambda *_: None  # noqa: E731
        r = wk.sync(web, hdb, embed=False, log=quiet)
        k = Knowledge(memory.connect(hdb))
        rows = k.con.execute("SELECT source_type, count(*) FROM knowledge WHERE status='active' GROUP BY 1").fetchall()
        check(r["added"] == 6 and dict(rows) == {"wiki": 4, "doc": 2}, f"first sync adds every chunk ({r}, {rows})")
        r = wk.sync(web, hdb, embed=False, log=quiet)
        conf = k.con.execute("SELECT sum(confirmations) FROM knowledge").fetchone()[0]
        check(r.get("unchanged") == 6 and not r.get("add") and conf == 0, f"a re-sync is a no-op ({r})")

        key = "wiki:7/colored_hatchets/uses/0"
        old = web.execute("SELECT knowledge_id FROM kb_entries WHERE key=?", (key,)).fetchone()[0]
        web.execute("UPDATE docs SET html=? WHERE key='wiki:7'",
                    (WIKI_HTML.replace("Exceptional: +250", "Exceptional: +300"),))
        web.commit()
        r = wk.sync(web, hdb, embed=False, log=quiet)
        new = web.execute("SELECT knowledge_id FROM kb_entries WHERE key=?", (key,)).fetchone()[0]
        check(r.get("update") == 1 and k.get(old)["status"] == "superseded" and k.get(new)["supersedes"] == old
              and "+300" in k.get(new)["content"], f"an edited section supersedes its entry ({r})")

        ore = web.execute("SELECT knowledge_id FROM kb_entries WHERE key='wiki:8/ore/0'").fetchone()[0]
        shelves = web.execute("SELECT knowledge_id FROM kb_entries WHERE key='news:5/storage_shelves/0'").fetchone()[0]
        k.confirm(shelves, source="observed", ref="capture x")
        web.execute("UPDATE docs SET html='<p>Moved.</p>' WHERE key='wiki:8'")
        web.execute("UPDATE docs SET html=? WHERE key='news:5'", (NEWS_HTML.split("<p><b>Provocation")[0],))
        web.execute("UPDATE docs SET html=? WHERE key='news:5'", ("<p><b>Provocation</b></p><ul><li>The maximum "
                                                                 "range is now 40 tiles</li></ul>",))
        web.commit()
        r = wk.sync(web, hdb, embed=False, log=quiet)
        check(k.get(ore)["status"] == "retracted" and k.get(ore)["retract_reason"].startswith("web-kb:"),
              "a removed section's entry is retracted")
        check(k.get(shelves)["status"] == "active", "... but not one the overseer confirmed")

        prov = web.execute("SELECT knowledge_id FROM kb_entries WHERE key='news:5/provocation/0'").fetchone()[0]
        k.retract(prov, "wrong on the live shard")
        r = wk.sync(web, hdb, embed=False, log=quiet)
        n = k.con.execute("SELECT count(*) FROM knowledge WHERE status='active' AND content LIKE '%40 tiles%'"
                          ).fetchone()[0]
        check(n == 0 and r.get("kept_out", 0) >= 1, f"an entry the overseer retracted stays out ({r})")

        before = k.con.execute("SELECT count(*), sum(confirmations) FROM knowledge").fetchone()
        web.execute("DELETE FROM kb_entries")      # state lost (or another computer's capture DB)
        web.commit()
        r = wk.sync(web, hdb, embed=False, log=quiet)
        after = k.con.execute("SELECT count(*), sum(confirmations) FROM knowledge").fetchone()
        check(before == after and not r.get("added") and not r.get("confirmed"),
              f"lost sync state: entries are adopted by their ref, not added or confirmed again ({r})")
        r = wk.sync(web, hdb, embed=False, log=quiet)
        check(not r.get("update") and not r.get("add"), f"... and the next sync is a no-op again ({r})")
        k.con.close()
        web.close()


def test_forum_resume():
    """An interrupted crawl listed every thread but fetched only the newest; the next crawl's
    listing walk stops at the first page with nothing new, and must still fetch the rest."""
    def listing(tid):
        return (f'<div class="structItem structItem--thread" data-author="a"><div class="structItem-title">'
                f'<a href="/index.php?threads/t.{tid}/">T{tid}</a></div><time data-timestamp="{tid}"></time>'
                f'<dt>Replies</dt><dd>0</dd></div><a href="/index.php?forums/patches.14/page-4">4</a>')

    class Fake:
        def __init__(self):
            self.urls = []

        def get(self, url):
            self.urls.append(url)
            base = f"{ow.FORUM}/index.php?forums/patches.14/"
            if url.startswith(base):
                page = int(url[len(base) + 5:]) if url != base else 1
                return listing(5 - page)          # page 1 holds thread 4 (newest), page 4 thread 1
            tid = int(url.rsplit(".", 1)[1].strip("/"))
            return post_html(tid * 10, "bob", body=f"post of {tid}")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        con = ow.connect(os.path.join(td, "web.db"))
        for tid in (4, 3, 2, 1):
            fetched = (tid, 0, 1.0) if tid >= 3 else (None, None, None)
            con.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (tid, 14, f"T{tid}", f"{ow.FORUM}/index.php?threads/t.{tid}/", "a", tid, tid, 0, *fetched))
        con.commit()
        fake = Fake()
        ow.crawl_forums(con, fake, nodes=[14])
        got = {r[0] for r in con.execute("SELECT thread_id FROM posts")}
        check(got == {1, 2} and not any("page-3" in u for u in fake.urls),
              f"a resumed crawl fetches the threads listed but never fetched ({sorted(got)}, {len(fake.urls)} GETs)")
        con.close()


def test_repeated_heading():
    """A patch post repeats a heading with near-identical text (Decode Map for treasure maps,
    then for resource maps): two entries, neither confirming the other."""
    sec = ("<p><b>Decode Map</b></p><ul><li>Players can decode a {} map by double-clicking it while "
           "at least 60 Cartography, then follow the arrow to the dig spot within the hour</li></ul>")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        web = web_db(td, news=sec.format("treasure") + sec.format("resource"))
        hdb = os.path.join(td, "harness.db")
        memory.connect(hdb).close()
        r = wk.sync(web, hdb, embed=False, log=lambda *_: None)
        con = memory.connect(hdb)
        rows = con.execute("SELECT topic, confirmations FROM knowledge WHERE topic LIKE '%Decode Map%'").fetchall()
        check(len(rows) == 2 and not r.get("confirmed") and all(c == 0 for _, c in rows)
              and len({t for t, _ in rows}) == 2, f"a repeated heading gets its own entry and topic ({rows}, {r})")
        con.close()
        web.close()


def main():
    for t in (test_text, test_forum_pages, test_forum_resume, test_chunks, test_sync, test_repeated_heading):
        print(f"== {t.__name__} ==")
        t()
    print(f"\nweb_kb: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

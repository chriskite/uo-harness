"""Outlands forums -> vetted game facts: the Discord KB pipeline (harness/discord_kb.py) run
over the forum posts that outlands_web.py captured (decision: docs/PLAN.md "Wiki, patch notes
and forums in the knowledge base").

The stages, LLM calls, cost guard, clustering, adjudication rules and promotion are
discord_kb's; this module only supplies the forum corpus (Forum) and its CLI:

  windows   one thread's posts in order (only posts dated before today, empty posts skipped),
            packed greedily from the thread start into parts of <= discord_kb.WINDOW_CHARS,
            one post per unit, so a new reply only changes the thread's last part. A post
            longer than a window becomes several consecutive units (pieces of at most
            WINDOW_CHARS // 2, split at its line breaks, else spaces), each with the post's
            prefix, so long patch notes are read whole; grounding checks the full post text.
            Lines are "[post_id] YYYY-MM-DD author (staff): text". The post text comes from
            outlands_web.to_text(strip_quotes=True), so a quoted earlier post is never
            attributed to the replier (a quote of quoted text fails grounding).
  official  a post by staff (posts.staff) in an official section (outlands_web.FORUM_NODES:
            Announcements, Active Development); a claim citing one is official.
  promote   like Discord (tag `forum`, ref `forum-kb:<fact id> <post link> (<first>..<last>)`),
            except that a fact whose newest supporting claim predates PROMOTE_SINCE is not
            promoted (and retracted if the pipeline added it and nobody confirmed it): the
            forums go back to 2017 and Outlands overhauled many systems in 2022-2024. Such
            facts stay in the state DB and the digest.

Forum claims cluster only among themselves: state lives in harness/data/forum_kb.db, not in
discord_kb.db. The capture DB is opened read-only.

  python harness/forum_kb.py run [--nodes 3,16] [--max-cost 40] [--db harness/data/harness.db]
  python harness/forum_kb.py extract [--nodes 3,16] [--limit N]
  python harness/forum_kb.py consolidate [--recluster]
  python harness/forum_kb.py promote [--db harness/data/harness.db] [--dry-run]
  python harness/forum_kb.py digest [--out docs/research/FORUM_KB.md]
  python harness/forum_kb.py search QUERY [--all] [-k 10]
  python harness/forum_kb.py stats
Global flags: --kb (state DB), --web (capture DB). --nodes takes node ids or section names.
"""

import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import discord_kb as kbm  # noqa: E402
import discord_search as ds  # noqa: E402
import outlands_web as ow  # noqa: E402

KB_DB = os.path.join(ow.DATA, "forum_kb.db")
DIGEST = os.path.join(ow.ROOT, "docs", "research", "FORUM_KB.md")
MAX_COST = 40.0                 # list-price USD per run
PROMOTE_SINCE = "2023-01-01"    # facts last seen before this aren't promoted (systems overhauled 2022-2024)

_DISCORD_HEAD = ('from a Discord chat log\n(channel #<CHANNEL>, <DAY>). '
                 'Each line is "[message_id] time author: text".')
_FORUM_HEAD = ('from a forum thread\n(section "<SECTION>", thread "<TITLE>"). '
               'Each line is "[message_id] date author: text"; "(staff)" after the author marks Outlands staff.')
assert _DISCORD_HEAD in kbm.EXTRACT_PROMPT, "discord_kb.EXTRACT_PROMPT header changed; update forum_kb"
EXTRACT_PROMPT = kbm.EXTRACT_PROMPT.replace(_DISCORD_HEAD, _FORUM_HEAD)


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()


def render(r):
    """The post's prompt lines: one, or for a post longer than a window, consecutive pieces of
    at most WINDOW_CHARS // 2 characters (prefix included) split at its line breaks, else at
    spaces, else hard. r: (id, node_id, thread_id, author, text, None, None, author_id, ts, staff)."""
    head = f"[{r[0]}] {r[8][:10]} {r[3] or '?'}{' (staff)' if r[9] else ''}: "
    lines = r[4].replace("\r\n", "\n").split("\n")
    line = head + " / ".join(lines)
    if len(line) < kbm.WINDOW_CHARS:
        return [line]
    room = max(1, kbm.WINDOW_CHARS // 2 - len(head))
    atoms = []   # (separator before it, text), each text <= room
    for i, ln in enumerate(lines):
        sep = " / " if i else ""
        if len(ln) <= room:
            atoms.append((sep, ln))
            continue
        for j, word in enumerate(ln.split(" ")):
            s = sep if j == 0 else " "
            while len(word) > room:
                atoms.append((s, word[:room]))
                s, word = "", word[room:]
            atoms.append((s, word))
    pieces = []
    for sep, a in atoms:
        if pieces and len(pieces[-1]) + len(sep) + len(a) <= room:
            pieces[-1] += sep + a
        else:
            pieces.append(a)
    return [head + p for p in pieces if p.strip()]


class Forum:
    """The forum corpus (the protocol is described above discord_kb.Msgs): a read-only view of
    outlands_web.db. Rows carry the post's staff flag as a 10th field."""

    tag = "forum"
    ref_prefix = "forum-kb:"
    promote_since = PROMOTE_SINCE
    default_names = tuple(ow.FORUM_NODES)
    title = "Outlands forum knowledge base"
    script = "harness/forum_kb.py"
    confidence_note = (
        "- Confidence: everything here is community-derived. `official` = stated by staff in an official "
        "section (" + ", ".join(n for n, off in ow.FORUM_NODES.values() if off) + "); `consensus` = at least "
        "two independent players agree and outnumber the dissent; `single_source` = one player said it, "
        f"unverified. Facts last seen before {PROMOTE_SINCE} are listed but not promoted to the knowledge "
        "store (Outlands overhauled many systems in 2022-2024). Verify in game before relying on it.")

    def __init__(self, path=ow.DEFAULT_DB):
        self.con = ds._open_msgs(path)

    def link(self, mid):
        return f"{ow.FORUM}/index.php?posts/{mid}/"

    def ref(self, fact, evidence):
        link = f" {self.link(evidence[0])}" if evidence else ""
        return f"{self.ref_prefix}{fact['id']}{link} ({fact['first_seen']}..{fact['last_seen']})"

    def range(self, node_ids):
        if not node_ids:
            return None, None
        lo, hi = self.con.execute(f"SELECT min(ts), max(ts) FROM posts WHERE node_id IN "
                                  f"({','.join('?' * len(node_ids))})", list(node_ids)).fetchone()
        return (_iso(lo) if lo is not None else None), (_iso(hi) if hi is not None else None)

    def describe(self, node_ids):
        return "sections " + ", ".join(ow.FORUM_NODES.get(n, (str(n),))[0] for n in node_ids)

    def resolve(self, names):
        by_name = {name.lower(): nid for nid, (name, _) in ow.FORUM_NODES.items()}
        out = []
        for n in names:
            nid = int(n) if str(n).isdigit() else by_name.get(str(n).strip().lower())
            if nid not in ow.FORUM_NODES:
                print(f"forum node {n}: unknown, skipped")
                continue
            if not self.con.execute("SELECT 1 FROM posts WHERE node_id=? LIMIT 1", (nid,)).fetchone():
                print(f"forum node {ow.FORUM_NODES[nid][0]}: no stored posts, skipped")
                continue
            out.append(nid)
        return out

    def thread_windows(self, thread_id, node_id, title, today):
        """Stable windows of one thread, from posts dated strictly before `today`."""
        name = ow.FORUM_NODES.get(node_id, (str(node_id),))[0]
        units = []
        for pid, author, author_id, staff, ts, html in self.con.execute(
                "SELECT id, author, author_id, staff, ts, html FROM posts WHERE thread_id=? ORDER BY position, id",
                (thread_id,)):
            if ts is None:
                continue
            iso = _iso(ts)
            text = ow.to_text(html, strip_quotes=True).strip()
            if iso[:10] >= today or not text:
                continue
            r = (pid, node_id, thread_id, author, text, None, None, author_id, iso, int(bool(staff)))
            units += [[(line, r)] for line in render(r)]
        out, idx, cur, size = [], 0, [], 0

        def flush():
            w = kbm._window(node_id, name, cur[0][0][1][8][:10], f"{thread_id}.{idx}", cur)
            w["title"] = title or ""
            out.append(w)

        for u in units:
            n = len(u[0][0]) + 1
            if cur and size + n > kbm.WINDOW_CHARS:
                flush()
                idx, cur, size = idx + 1, [], 0
            cur.append(u)
            size += n
        if cur:
            flush()
        return out

    def windows(self, names, today):
        nodes = self.resolve(names)
        out = []
        for nid in nodes:
            for tid, title in self.con.execute("SELECT id, title FROM threads WHERE node_id=? ORDER BY id",
                                               (nid,)).fetchall():
                out += self.thread_windows(tid, nid, title, today)
        return out, nodes

    def is_official(self, row):
        return bool(row[9]) and ow.FORUM_NODES.get(row[1], ("", False))[1]

    def extract_prompt(self, w):
        lines = "\n".join(line for u in w["units"] for line, _ in u)
        return EXTRACT_PROMPT.replace("<SECTION>", w["name"]).replace("<TITLE>", w["title"]) + "\n" + lines + "\n"


def main():
    ap = argparse.ArgumentParser(description="Outlands forums -> vetted facts (discord_kb pipeline)")
    ap.add_argument("--kb", default=KB_DB, help="pipeline state DB")
    ap.add_argument("--web", default=ow.DEFAULT_DB, help="outlands_web capture DB (read-only)")
    kbm.add_commands(ap.add_subparsers(dest="cmd", required=True), "--nodes",
                     "comma-separated forum node ids or section names (default: all of outlands_web.FORUM_NODES)",
                     MAX_COST, kbm.HARNESS_DB, DIGEST)
    a = ap.parse_args()
    return kbm.dispatch(a, kbm.open_kb(a.kb), Forum(a.web))


if __name__ == "__main__":
    sys.exit(main())

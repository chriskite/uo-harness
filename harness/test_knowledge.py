"""Tests for harness/knowledge.py: the overseer's long-term memory.

What a consumer relies on: duplicates become confirmations, related entries
surface on write, edits keep history, retractions need reasons, ranked recall
(relevance, location, importance, recency), filters, safe queries, the
situational brief and the review; with an embedder, recall by meaning that keeps
the filters and embeds new or changed entries once. Offline, temp store, fake
clock, fake embedder (concept axes instead of the model).

Run: python harness/test_knowledge.py
"""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import memory  # noqa: E402
from knowledge import Knowledge, KnowledgeError  # noqa: E402

FAILURES = []
DAY = 86400.0


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def fresh():
    clock = Clock()
    return Knowledge(memory.connect(os.path.join(tempfile.mkdtemp(), "harness.db")), now=clock), clock


def raises(fn):
    try:
        fn()
    except KnowledgeError:
        return True
    return False


def test_write_discipline():
    print("== write discipline ==")
    k, clock = fresh()
    a = k.add("fact", "hatchet price", "Subrey the provisioner sells hatchets for 25 gp",
              tags="vendor,shelter", entities=["Subrey"], at=(0, 1977, 2565), source="observed",
              ref="chat#40", importance=6)
    check("add returns the new id", a["action"] == "added" and a["id"] == 1, str(a))
    e = k.get(a["id"])
    check("provenance, default confidence by source, normalised tags, location",
          (e["source_type"], e["source_ref"], e["confidence"], e["tags"], (e["facet"], e["x"], e["y"]))
          == ("observed", "chat#40", 0.9, ["shelter", "vendor"], (0, 1977, 2565)), str(e))
    b = k.add("fact", "hatchet price", "subrey the provisioner sells HATCHETS for 25 gp.", source="observed")
    e = k.get(a["id"])
    check("the same content again confirms instead of copying", b == {"id": 1, "action": "confirmed",
                                                                        "related": []}
          and e["confirmations"] == 1 and e["confidence"] > 0.9, f"{b} {e['confidence']}")
    c = k.add("fact", "hatchet price", "Subrey the provisioner sells hatchets for 25 gp each")
    check("near-identical wording on the same topic also confirms", c["action"] == "confirmed" and c["id"] == 1,
          str(c))
    d = k.add("fact", "hatchet price", "Crystal the tinker sells hatchets for 25 gp too")
    check("a different fact on the same topic is added, with the existing one as related",
          d["action"] == "added" and [r["id"] for r in d["related"]] == [1], str(d))
    check("same content under another kind is its own entry",
          k.add("insight", "hatchet price", "Subrey the provisioner sells hatchets for 25 gp")["action"] == "added")
    check("inferred entries start at low confidence",
          k.get(k.add("fact", "npd", "the NPD entrance leads to a dungeon on facet 0",
                      source="inferred")["id"])["confidence"] == 0.5)
    check("community entries (Discord-mined) start at 0.6",
          k.get(k.add("fact", "copperwood", "Copperwood logs need 75 lumberjacking to chop",
                      source="community")["id"])["confidence"] == 0.6)
    for bad in (lambda: k.add("rumour", "t", "c"), lambda: k.add("fact", "", "c"),
                lambda: k.add("fact", "t", "c", importance=11), lambda: k.add("fact", "t", "c", source="guess"),
                lambda: k.add("fact", "t", "c", confidence=1.5)):
        check("invalid write refused", raises(bad))


def test_versions():
    print("== versions: supersede, history, retract ==")
    k, clock = fresh()
    i = k.add("fact", "recall scroll price", "Recall scrolls cost 150 gp at the mage", source="wiki")["id"]
    clock.t += 3600
    u = k.update(i, content="Recall scrolls cost 200 gp at Sherwin the mage and Zara the scribe",
                 source="observed", confidence=0.95)
    old, new = k.get(i), k.get(u["id"], history=True)
    check("a content change makes a new version that supersedes the old",
          u["action"] == "superseded" and old["status"] == "superseded" and old["superseded_by"] == u["id"]
          and new["supersedes"] == i and new["source_type"] == "observed", f"{u} {old['status']}")
    check("history is readable from the new version",
          [h["id"] for h in new["history"]] == [i] and "150" in new["history"][0]["content"])
    check("superseded entries don't come back in search",
          [e["id"] for e in k.search("recall scroll price")] == [u["id"]])
    check("... unless asked for",
          {e["id"] for e in k.search("recall scroll price", include_inactive=True)} == {i, u["id"]})
    m = k.update(u["id"], importance=9, tags=["prices"])
    check("metadata edits happen in place", m["action"] == "updated" and k.get(u["id"])["importance"] == 9
          and k.get(u["id"])["tags"] == ["prices"])
    check("a retraction needs a reason", raises(lambda: k.retract(u["id"], "  ")))
    k.retract(u["id"], "prices changed with the patch")
    e = k.get(u["id"])
    check("retracted, reason kept, gone from search",
          e["status"] == "retracted" and e["retract_reason"] == "prices changed with the patch"
          and k.search("recall") == [])
    check("editing a retracted entry is refused", raises(lambda: k.update(u["id"], importance=1)))


def test_recall():
    print("== ranked recall ==")
    k, clock = fresh()
    far = k.add("fact", "trees", "Dullwood trees grow near the Shelter inn", at=(0, 1924, 2588), importance=5)["id"]
    clock.t += 1
    near = k.add("fact", "trees", "Copperwood trees grow west of the moongate hill", at=(0, 1960, 2540),
                 importance=5)["id"]
    proc = k.add("procedure", "resurrection", "Walk the ghost to a healer; the resurrection gump opens within "
                 "2 tiles; after Decline it is re-offered when you come back within 4 tiles",
                 tags="death", importance=9)["id"]
    pref = k.add("preference", "death policy", "No corpse runs and no [TestRes", source="user", importance=9)["id"]
    check("stemmed relevance: 'resurrecting healers' finds the procedure",
          [e["id"] for e in k.search("resurrecting healers")][:1] == [proc])
    check("location boost: 'trees' near the moongate ranks the copperwood first",
          [e["id"] for e in k.search("trees", near=(0, 1962, 2542))][:2] == [near, far])
    check("... and near the inn the dullwood first",
          [e["id"] for e in k.search("trees", near=(1925, 2590))][:2] == [far, near])
    check("kind and tag filters", [e["id"] for e in k.search("", kind="preference")] == [pref]
          and [e["id"] for e in k.search(None, tags="death")] == [proc])
    check("punctuation and FTS syntax in queries are harmless",
          k.search('corpse" OR ) NEAR(') != [] and k.search("*** ???") == [])
    before = k.get(proc)["access_count"]
    k.search("healer")
    check("recall counts as an access", k.get(proc)["access_count"] == before + 1)
    old = k.add("fact", "gate", "The moongate opens a destinations gump", importance=5)["id"]
    clock.t += 60 * 86400
    new = k.add("fact", "gate", "The moongate gump lists twelve destinations", importance=5)["id"]
    check("recency: of two equally relevant facts, the fresh one ranks first",
          [e["id"] for e in k.search("moongate gump destinations")][:2] == [new, old])


def test_brief_review():
    print("== brief and review ==")
    k, clock = fresh()
    inn = k.add("fact", "innkeeper", "Jayne the innkeeper rents rooms; say room within 4 tiles",
                entities=["Jayne"], at=(0, 1932, 2595), importance=7)["id"]
    npd = k.add("episode", "death", "Died to a crowd of mongbats and frogs in the NPD at 5524,519",
                at=(0, 5524, 519), importance=6)["id"]
    pref = k.add("preference", "gold", "Spending cap 50000 gp per day", source="user", importance=8)["id"]
    low = k.add("procedure", "misc", "Close shop windows yourself", importance=3)["id"]
    b = k.brief({"pos": [1933, 2596, 21], "facet": 0, "mobiles": ["Jayne the innkeeper"],
                 "junctures": [], "intent": "Going home: heading to the innkeeper"})
    rel = [e["id"] for e in b["relevant"]]
    check("brief: what matters here (the innkeeper) first", rel[:1] == [inn], str(b["relevant"]))
    check("brief: standing preferences/procedures of importance >= 7 are always included",
          pref in [e["id"] for e in b["standing"]] and low not in [e["id"] for e in b["standing"]], str(b["standing"]))
    check("brief: a far-away episode isn't ranked above the local fact",
          npd not in rel or rel.index(npd) > rel.index(inn))
    guess = k.add("fact", "gate", "The renounce prompt probably follows Travel", source="inferred")["id"]
    k.add("fact", "innkeeper", "Jayne stands behind the counter at 1932,2595")
    clock.t += 40 * DAY
    r = k.review()
    check("review: unconfirmed inferences", [e["id"] for e in r["unconfirmed_inferences"]] == [guess])
    check("review: topics with several active facts", "innkeeper" in r["topics_with_several_facts"])
    check("review: stale = never recalled for 30+ days", guess in [e["id"] for e in r["stale"]])
    s = k.stats()
    check("stats by kind and status", s["by_kind"]["fact"]["active"] == 3 and s["total"] == 6, str(s))


def test_pinned():
    print("== pinned: the standing memories every brief returns ==")
    k, clock = fresh()
    lib = k.add("procedure", "guild rune library", "Go to the DTF guild house tomes for places our book lacks",
                importance=7, tags=["travel"])["id"]
    for i in range(15):
        k.add("preference", f"rule {i}", f"Standing rule number {i} about something else", source="user", importance=10)
    b = k.brief({}, limit=12)
    check("unpinned, an importance-7 procedure is crowded out of standing by 15 importance-10 rules",
          lib not in [e["id"] for e in b["standing"]] and not b["pinned"])
    check("pin", k.pin(lib)["action"] == "pinned" and "travel" in k.get(lib)["tags"])
    clock.t += 90 * DAY
    b = k.brief({}, limit=12)
    check("pinned: returned in full in every brief, however crowded or old, and in no other list",
          [e["id"] for e in b["pinned"]] == [lib] and b["pinned"][0]["content"].startswith("Go to the DTF")
          and lib not in [e["id"] for e in b["standing"] + b["relevant"]] and len(b["standing"]) == 12, str(b["pinned"]))
    new = k.update(lib, content="Go to the DTF guild house tomes (4152,1429) for places our book lacks")["id"]
    check("a new version stays pinned; the superseded one leaves the brief",
          [e["id"] for e in k.brief({})["pinned"]] == [new])
    check("unpin", k.pin(new, on=False)["action"] == "unpinned" and not k.brief({})["pinned"]
          and k.get(new)["tags"] == ["travel"])
    try:
        k.pin(lib)
        refused = False
    except KnowledgeError:
        refused = True
    check("a superseded entry can't be pinned", refused)


def test_brief_scope():
    print("== brief: only this character's entries, and only what the situation calls up ==")
    k, clock = fresh()
    general = k.add("preference", "combat rule", "Never attack players", source="user", importance=9)["id"]
    hack = k.add("preference", "hackworth lumber goal", "Lumber on Shelter until 5000 boards", source="user",
                 importance=9, tags=["char:hackworth"])["id"]
    dan = k.add("procedure", "guild library", "Use the DTF guild tomes", importance=8, tags=["char:outland_dan"])["id"]
    k.pin(dan)
    k.pin(hack)
    npd = k.add("procedure", "npd gold farming", "Stand on the NPD exit tile and pull mongbats",
                importance=8, at=(0, 5535, 529))["id"]
    stable = k.add("fact", "shelter stablemaster prices", "Carlton the stablemaster sells horses for 750 gp",
                   importance=7)["id"]
    karmina = k.add("fact", "alchemist", "Karmina sells potions in bulk", entities=["Karmina"])["id"]
    word = k.add("fact", "alchemy", "An alchemist named nothing in particular", importance=9)["id"]
    here = k.add("fact", "guild house door", "The guild house door is at the east wall", at=(0, 4160, 1430))["id"]
    far = k.add("fact", "far grove", "A grove 100 tiles away", at=(0, 4252, 1429), importance=9)["id"]
    sit = {"character": "Outland Dan", "pos": [4152, 1429, 6], "facet": 0,
           "mobiles": ["Karmina the alchemist", "PizzaParty the stablemaster"], "intent": "", "junctures": []}
    b = k.brief(sit)
    ids = lambda key: [e["id"] for e in b[key]]  # noqa: E731
    check("another character's entries are out of every list, its pin too; untagged ones stay",
          ids("pinned") == [dan] and hack not in ids("standing") + ids("relevant") and general in ids("standing"),
          str(b))
    check("a located procedure isn't a standing rule anywhere (it comes up where it is)", npd not in ids("standing"))
    check("relevant = what is here and who is here: the entry about Karmina (by name) and the one 8 tiles off; "
          "not titles, far places or important filler",
          sorted(ids("relevant")) == sorted([karmina, here]) and stable not in ids("relevant")
          and word not in ids("relevant") and far not in ids("relevant"), str(b["relevant"]))
    b = k.brief({**sit, "character": "Hackworth"})
    check("as Hackworth: his goal and pin come back, Dan's don't",
          [e["id"] for e in b["pinned"]] == [hack] and dan not in [e["id"] for e in b["standing"]])
    b = k.brief({})
    check("character unknown (proxy down): every entry applies", {dan, hack} <= {e["id"] for e in b["pinned"]})


class FakeEmbedder:
    """Words map to concept axes, so 'dying' and 'resurrect' meet without sharing a word."""
    MODEL = "fake-concepts"
    AXES = ({"die", "dying", "died", "dead", "death", "ghost", "resurrect", "healer"},
            {"tree", "trees", "wood", "chop", "lumber", "dullwood", "copperwood"},
            {"bank", "gold", "banker", "vault"})

    def __init__(self):
        self.embedded = 0

    def _vec(self, text):
        ws = {w.strip(".,:;!?").lower() for w in text.split()}
        v = np.array([float(len(ws & ax)) for ax in self.AXES] + [0.05], dtype=np.float32)
        return v / np.linalg.norm(v)

    def passages(self, texts):
        self.embedded += len(texts)
        return np.vstack([self._vec(t) for t in texts])

    def query(self, text):
        return self._vec(text)


def test_semantic():
    print("== semantic recall (fake embedder) ==")
    clock = Clock()
    con = memory.connect(os.path.join(tempfile.mkdtemp(), "harness.db"))
    emb = FakeEmbedder()
    k, words_only = Knowledge(con, now=clock, embed=emb), Knowledge(con, now=clock)
    proc = k.add("procedure", "resurrect", "Walk a ghost to a healer; the gump opens within 2 tiles")["id"]
    tree = k.add("fact", "trees", "Dullwood trees grow near the Shelter inn")["id"]
    old = k.add("fact", "death penalty", "Death costs 10% of skills when you die murdered")["id"]
    k.retract(old, "wrong: no skill loss")
    q = "what do I do after dying"
    check("word-only recall can't find it: no shared word", words_only.search(q, touch=False) == [])
    res = k.search(q, touch=False)
    check("recall by meaning: the resurrect procedure first, with its similarity",
          res and res[0]["id"] == proc and res[0]["similarity"] > 0.9, str(res[:2]))
    check("retracted entries stay out unless asked for",
          old not in [e["id"] for e in res] and old in [e["id"] for e in k.search(q, include_inactive=True,
                                                                                   touch=False)])
    check("the kind filter holds in the meaning ranking",
          [e["kind"] for e in k.search(q, kind="fact", touch=False)] == ["fact"])
    n = emb.embedded
    k.search("chop wood", touch=False)
    check("unchanged entries aren't embedded again", emb.embedded == n, f"{n} -> {emb.embedded}")
    bank = k.add("procedure", "banking", "Say bank to the banker to open your vault")["id"]
    res = k.search("where do I keep my gold", touch=False)
    check("an entry added after the last search is embedded and found",
          emb.embedded == n + 1 and res[0]["id"] == bank, str(res[:1]))
    check("a word match still counts: 'dullwood' finds the tree fact first",
          k.search("dullwood", touch=False)[0]["id"] == tree)
    b = k.brief({"intent": "my character died, find a healer", "junctures": []})
    check("brief by meaning: a dying intent calls up the resurrect procedure, not the unrelated tree/bank facts",
          [e["id"] for e in b["relevant"]] == [proc], str(b["relevant"]))


def main():
    for t in (test_write_discipline, test_versions, test_recall, test_brief_review, test_pinned, test_brief_scope,
              test_semantic):
        t()
    print(f"\nknowledge: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

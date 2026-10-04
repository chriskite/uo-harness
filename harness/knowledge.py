"""The overseer's long-term memory: what it learns about the game and its
interactions over time (docs/MEMORY.md "Knowledge"). Runtime data in the
memory store (harness/data/harness.db, gitignored); exposed as `ctl know`.

Kinds (what the entry is for):
  fact        semantic: "Subrey the provisioner sells hatchets for 25 gp"
  procedure   how to do something: "resurrect: walk a ghost to a healer; the gump
              opens within 2 tiles; if closed, it's re-offered when you come back
              within 4"
  episode     something that happened: "10:18 died to a mob crowd in the NPD"
  preference  a standing user directive: "no corpse runs"
  insight     a lesson generalised from episodes: "standing still in the NPD,
              weak mobs die to auto-retaliation, a crowd kills"

Write discipline (the usual agent-memory hygiene):
  - provenance on every entry: source_type observed | user | wiki | doc |
    community | inferred, plus a ref (capture tag, chat id, juncture id, URL).
    Confidence defaults by source (observed 0.9, user 0.95, doc 0.8, wiki 0.7,
    community 0.6, inferred 0.5). community = claims mined from the Outlands
    Discord by harness/discord_kb.py.
  - no silent duplicates: the same content again (normalised) is a
    confirmation of the existing entry (confirmations += 1, confidence up).
    Every add also returns `related` entries (same topic or similar words) so
    the writer can supersede instead of piling up contradictions.
  - history, not overwrites: changing content creates a new version that
    supersedes the old one (status superseded, still readable with `get`).
    Wrong entries are retracted with a reason, never deleted.
  - importance 1..10 at write time (how much it matters if forgotten).

Retrieval (Generative Agents style): relevance, recency (half-life
RECENCY_HALF_LIFE_D, from the last update or access), importance, confidence,
and a location boost for entries near a given tile. Relevance is hybrid when
the Knowledge has an embedder (ctl passes harness/embedder.py, bge-small on the
GPU): the CANDIDATES nearest entries by meaning (cosine of "topic: content"
vectors, kept in knowledge_vec and refreshed before each search) and the
CANDIDATES best FTS5 hits (BM25, porter stemming) fused by reciprocal rank, so
"what do I do after dying" finds the resurrect procedure without sharing a word.
Without an embedder it is FTS5 alone. Every result returned counts as an access.
`brief()` builds the query from the current situation (position, nearby NPC
names, open junctures, the current intent) and adds the most important
procedures and preferences. Entries tagged PIN_TAG ("pinned": `pin()`) are the
standing memories the overseer must recall when it starts: brief() returns
every one of them in full, ahead of and apart from the capped lists.
"""
import hashlib
import json
import math
import re
import time

import numpy as np

KINDS = ("fact", "procedure", "episode", "preference", "insight")
SOURCES = {"observed": 0.9, "user": 0.95, "doc": 0.8, "wiki": 0.7, "community": 0.6, "inferred": 0.5}
RECENCY_HALF_LIFE_D = 14.0
NEAR_RADIUS = 40                     # tiles: location boost fades to 0 at this distance
W_REL, W_REC, W_IMP, W_CONF, W_NEAR = 1.0, 0.4, 0.5, 0.3, 0.6
DUP_JACCARD = 0.85                   # near-identical wording counts as the same entry
RELATED_JACCARD = 0.35
CONFIRM_STEP = 0.5                   # a confirmation closes this share of the gap to 1.0
CANDIDATES = 60                      # hybrid recall: entries taken from each ranking
RRF_K = 60                           # reciprocal rank fusion constant
PIN_TAG = "pinned"                   # must-recall at overseer start: brief() lists all of them
CHAR_PREFIX = "char:"                # char:<name> tags scope an entry to those characters (char_tag)
BRIEF_MIN_SIM = 0.65                 # brief: a situation (intent/juncture) match must mean the same.
# A bag of NPC titles ("PizzaParty the stablemaster ...") reached cosine 0.70 against far-away
# Shelter stablemaster facts (live 2026-10-04), so names in view match by exact phrase instead.
COLS = ("id", "kind", "topic", "content", "tags", "entities", "facet", "x", "y", "source_type",
        "source_ref", "confidence", "importance", "status", "supersedes", "superseded_by",
        "retract_reason", "confirmations", "created_t", "updated_t", "last_access_t", "access_count")
_WORD = re.compile(r"[A-Za-z0-9_]+")
_STOP = frozenset("a an the of to in on at for and or is are was were be by with from it this that as "
                  "we i you he she they them his her its our your".split())


class KnowledgeError(ValueError):
    pass


def words(text: str) -> list[str]:
    return [w for w in (m.lower() for m in _WORD.findall(text or "")) if w not in _STOP]


def norm_tags(tags) -> str:
    if isinstance(tags, str):
        tags = re.split(r"[,\s]+", tags)
    return " ".join(sorted({t.strip().lower() for t in (tags or ()) if t and t.strip()}))


def content_hash(kind: str, content: str) -> str:
    return hashlib.sha1(f"{kind}\n{' '.join(words(content))}".encode()).hexdigest()


def jaccard(a: str, b: str) -> float:
    sa, sb = set(words(a)), set(words(b))
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def fts_query(text: str) -> str | None:
    """A safe FTS5 query: the text's words, quoted, ORed (no syntax injection)."""
    ws = sorted(set(words(text)))
    return " OR ".join(f'"{w}"' for w in ws) if ws else None


def char_tag(name: str) -> str:
    """The scope tag of a character: "Outland Dan" -> "char:outland_dan"."""
    return CHAR_PREFIX + re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")


def for_character(e: dict, character: str | None) -> bool:
    """An entry applies to `character`: it has no char: tag, or one of them is its own.
    Unknown character (proxy down): everything applies."""
    scopes = [t for t in e["tags"] if t.startswith(CHAR_PREFIX)]
    return not scopes or not character or char_tag(character) in scopes


def proper_name(label: str) -> str:
    """The name part of a mobile's label: "Karmina the alchemist" -> "Karmina"."""
    return re.split(r"\s+the\s+", label or "", maxsplit=1)[0].strip()


def _row(r) -> dict:
    d = dict(zip(COLS, r))
    d["entities"] = json.loads(d["entities"] or "[]")
    d["tags"] = d["tags"].split() if d["tags"] else []
    return d


class Knowledge:
    """Knowledge entries in a memory-store connection (memory.connect).
    embed: None for word-only recall, or an embedder with MODEL, passages(texts)
    and query(text) returning L2-normalised vectors (harness/embedder.py)."""

    def __init__(self, con, now=time.time, embed=None):
        self.con = con
        self.now = now
        self.embed = embed

    # ------------------------------------------------------------------ write
    def add(self, kind: str, topic: str, content: str, *, tags=(), entities=(), at=None,
            source: str = "observed", ref: str | None = None, confidence: float | None = None,
            importance: int = 5, supersedes: int | None = None) -> dict:
        """Store an entry. Returns {"id", "action": "added"|"confirmed"|"superseded",
        "related": [...]}. The same content (normalised) as an active entry of the
        same kind confirms it instead of adding a copy."""
        if kind not in KINDS:
            raise KnowledgeError(f"kind must be one of {KINDS}")
        if source not in SOURCES:
            raise KnowledgeError(f"source must be one of {tuple(SOURCES)}")
        topic, content = (topic or "").strip(), (content or "").strip()
        if not topic or not content:
            raise KnowledgeError("topic and content are required")
        if not 1 <= int(importance) <= 10:
            raise KnowledgeError("importance is 1..10")
        conf = SOURCES[source] if confidence is None else float(confidence)
        if not 0.0 <= conf <= 1.0:
            raise KnowledgeError("confidence is 0..1")
        facet = x = y = None
        if at is not None:
            facet, x, y = (0, at[0], at[1]) if len(at) == 2 else (at[0], at[1], at[2])
        h = content_hash(kind, content)
        dup = None
        if supersedes is None:
            dup = self.con.execute("SELECT id FROM knowledge WHERE content_hash=? AND status='active'",
                                   (h,)).fetchone()
            if dup is None:
                for cand in self._candidates(kind, topic, content):
                    if cand["topic"].lower() == topic.lower() and jaccard(cand["content"], content) >= DUP_JACCARD:
                        dup = (cand["id"],)
                        break
        if dup is not None:
            self.confirm(dup[0], source=source, ref=ref)
            return {"id": dup[0], "action": "confirmed", "related": self.related(kind, topic, content, dup[0])}
        if supersedes is not None:
            old = self.get(supersedes)
            if old is None or old["status"] != "active":
                raise KnowledgeError(f"#{supersedes} isn't an active entry")
        t = self.now()
        cur = self.con.execute(
            "INSERT INTO knowledge(kind, topic, content, tags, entities, facet, x, y, source_type, source_ref, "
            "confidence, importance, supersedes, content_hash, created_t, updated_t) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (kind, topic, content, norm_tags(tags), json.dumps(sorted({str(e) for e in entities})),
             facet, x, y, source, ref, conf, int(importance), supersedes, h, t, t))
        new_id = cur.lastrowid
        if supersedes is not None:
            self.con.execute("UPDATE knowledge SET status='superseded', superseded_by=?, updated_t=? WHERE id=?",
                             (new_id, t, supersedes))
        self.con.commit()
        return {"id": new_id, "action": "superseded" if supersedes else "added",
                "related": self.related(kind, topic, content, new_id)}

    def update(self, kid: int, *, content=None, topic=None, tags=None, entities=None, at=None,
               confidence=None, importance=None, source=None, ref=None) -> dict:
        """Metadata edits in place; a content or topic change makes a new
        version that supersedes this one (history is kept)."""
        old = self.get(kid)
        if old is None or old["status"] != "active":
            raise KnowledgeError(f"#{kid} isn't an active entry")
        if content is not None or topic is not None:
            loc = None if old["x"] is None else (old["facet"], old["x"], old["y"])
            return self.add(old["kind"], topic or old["topic"], content or old["content"],
                            tags=old["tags"] if tags is None else tags,
                            entities=old["entities"] if entities is None else entities,
                            at=loc if at is None else at, source=source or old["source_type"],
                            ref=ref if ref is not None else old["source_ref"],
                            confidence=old["confidence"] if confidence is None else confidence,
                            importance=old["importance"] if importance is None else importance,
                            supersedes=kid)
        sets, args = [], []
        for col, val in (("tags", None if tags is None else norm_tags(tags)),
                         ("entities", None if entities is None else json.dumps(sorted({str(e) for e in entities}))),
                         ("confidence", confidence), ("importance", importance)):
            if val is not None:
                sets.append(f"{col}=?")
                args.append(val)
        if at is not None:
            sets += ["facet=?", "x=?", "y=?"]
            args += list(at if len(at) == 3 else (0, at[0], at[1]))
        if not sets:
            raise KnowledgeError("nothing to update")
        sets.append("updated_t=?")
        args += [self.now(), kid]
        self.con.execute(f"UPDATE knowledge SET {', '.join(sets)} WHERE id=?", args)
        self.con.commit()
        return {"id": kid, "action": "updated"}

    def pin(self, kid: int, on: bool = True) -> dict:
        """Make an active entry a standing memory brief() always returns (the PIN_TAG
        tag; a later new version keeps it), or stop that (on=False)."""
        e = self.get(kid)
        if e is None or e["status"] != "active":
            raise KnowledgeError(f"#{kid} isn't an active entry")
        tags = (set(e["tags"]) | {PIN_TAG}) if on else (set(e["tags"]) - {PIN_TAG})
        if tags == set(e["tags"]):
            return {"id": kid, "action": "unchanged", "pinned": on}
        self.update(kid, tags=sorted(tags))
        return {"id": kid, "action": "pinned" if on else "unpinned", "pinned": on}

    def pinned(self) -> list[dict]:
        """Every active pinned entry, most important first, then oldest."""
        rows = [_row(r) for r in self.con.execute(
            f"SELECT {', '.join(COLS)} FROM knowledge WHERE status='active' AND (' ' || tags || ' ') LIKE ?",
            (f"% {PIN_TAG} %",)).fetchall()]
        return sorted(rows, key=lambda e: (-e["importance"], e["id"]))

    def confirm(self, kid: int, source: str = "observed", ref: str | None = None) -> dict:
        """Seen true again: confirmations += 1, confidence closes CONFIRM_STEP of
        the gap to 1 (capped by the new evidence's source level + 0.05)."""
        e = self.get(kid)
        if e is None or e["status"] != "active":
            raise KnowledgeError(f"#{kid} isn't an active entry")
        cap = min(1.0, SOURCES.get(source, 0.5) + 0.05)
        conf = max(e["confidence"], min(cap, e["confidence"] + (1.0 - e["confidence"]) * CONFIRM_STEP))
        self.con.execute("UPDATE knowledge SET confirmations=confirmations+1, confidence=?, updated_t=?, "
                         "source_ref=COALESCE(?, source_ref) WHERE id=?", (conf, self.now(), ref, kid))
        self.con.commit()
        return {"id": kid, "action": "confirmed", "confidence": round(conf, 3),
                "confirmations": e["confirmations"] + 1}

    def retract(self, kid: int, reason: str) -> dict:
        if not (reason or "").strip():
            raise KnowledgeError("a retraction needs a reason")
        e = self.get(kid)
        if e is None or e["status"] != "active":
            raise KnowledgeError(f"#{kid} isn't an active entry")
        self.con.execute("UPDATE knowledge SET status='retracted', retract_reason=?, updated_t=? WHERE id=?",
                         (reason.strip(), self.now(), kid))
        self.con.commit()
        return {"id": kid, "action": "retracted"}

    # ------------------------------------------------------------------- read
    def get(self, kid: int, history: bool = False) -> dict | None:
        r = self.con.execute(f"SELECT {', '.join(COLS)} FROM knowledge WHERE id=?", (kid,)).fetchone()
        if r is None:
            return None
        e = _row(r)
        if history:
            chain, prev = [], e["supersedes"]
            while prev is not None and len(chain) < 50:
                p = self.get(prev)
                if p is None:
                    break
                chain.append({k: p[k] for k in ("id", "content", "confidence", "created_t", "status")})
                prev = p["supersedes"]
            e["history"] = chain
        return e

    def _candidates(self, kind, topic, content, limit=20) -> list[dict]:
        q = fts_query(f"{topic} {content}")
        if q is None:
            return []
        rows = self.con.execute(
            f"SELECT {', '.join('k.' + c for c in COLS)} FROM knowledge_fts f JOIN knowledge k ON k.id = f.rowid "
            "WHERE knowledge_fts MATCH ? AND k.status='active' AND k.kind=? ORDER BY bm25(knowledge_fts) LIMIT ?",
            (q, kind, limit)).fetchall()
        return [_row(r) for r in rows]

    def related(self, kind, topic, content, exclude=None, limit=3) -> list[dict]:
        """Active entries of the kind about the same topic or with similar wording."""
        out = []
        for c in self._candidates(kind, topic, content):
            if c["id"] == exclude:
                continue
            sim = jaccard(c["content"], content)
            if c["topic"].lower() == topic.lower() or sim >= RELATED_JACCARD:
                out.append({"id": c["id"], "kind": c["kind"], "topic": c["topic"], "content": c["content"],
                            "confidence": c["confidence"], "similarity": round(sim, 2)})
        return out[:limit]

    def search(self, query: str | None = None, *, kind=None, tags=(), near=None, limit: int = 10,
               include_inactive: bool = False, touch: bool = True) -> list[dict]:
        """Ranked recall. near = (facet, x, y) or (x, y) boosts entries located
        close by. Without a query, filters rank by importance/recency/confidence."""
        where, args = [], []
        if not include_inactive:
            where.append("k.status='active'")
        if kind:
            kinds = [kind] if isinstance(kind, str) else list(kind)
            where.append(f"k.kind IN ({','.join('?' * len(kinds))})")
            args += kinds
        for t in ([tags] if isinstance(tags, str) else tags):
            if t:
                where.append("(' ' || k.tags || ' ') LIKE ?")
                args.append(f"% {t.strip().lower()} %")
        cols = ", ".join("k." + c for c in COLS)
        q = fts_query(query) if query else None
        filt = "".join(f" AND {w}" for w in where)
        if query and self.embed is not None:
            cands = self._hybrid(query, q, cols, filt, args)
        elif q:
            rows = self.con.execute(f"SELECT {cols}, -bm25(knowledge_fts, 3.0, 1.0, 2.0, 2.0) FROM knowledge_fts f "
                                    f"JOIN knowledge k ON k.id = f.rowid WHERE knowledge_fts MATCH ?{filt} LIMIT 200",
                                    [q] + args).fetchall()
            top = max((r[-1] for r in rows), default=0.0) or 1.0
            cands = [(r[:-1], r[-1] / top, None) for r in rows]
        elif query:
            return []
        else:
            sql = f"SELECT {cols} FROM knowledge k" + (f" WHERE {' AND '.join(where)}" if where else "") \
                + " LIMIT 1000"
            cands = [(r, 0.0, None) for r in self.con.execute(sql, args).fetchall()]
        if not cands:
            return []
        scored = self._score(cands, near)
        out = scored[:limit]
        if touch:
            self._touch(out)
        return out

    def _score(self, cands, near) -> list[dict]:
        """Entries from (row, relevance 0..1, cosine or None), scored and ranked."""
        now = self.now()
        if near is not None and len(near) == 2:
            near = (0, near[0], near[1])
        scored = []
        for r, rel, sim in cands:
            e = _row(r)
            if sim is not None:
                e["similarity"] = round(sim, 3)
            age_d = (now - max(e["updated_t"], e["last_access_t"] or 0)) / 86400.0
            rec = math.exp(-age_d * math.log(2) / RECENCY_HALF_LIFE_D)
            prox, dist = 0.0, None
            if near is not None and e["x"] is not None and (e["facet"] or 0) == (near[0] or 0):
                dist = max(abs(e["x"] - near[1]), abs(e["y"] - near[2]))
                prox = max(0.0, 1.0 - dist / NEAR_RADIUS)
            score = W_REL * rel + W_REC * rec + W_IMP * e["importance"] / 10 + W_CONF * e["confidence"] \
                + W_NEAR * prox
            e["score"] = round(score, 3)
            if dist is not None:
                e["dist"] = dist
            scored.append(e)
        scored.sort(key=lambda e: (-e["score"], -e["id"]))
        return scored

    def _touch(self, entries):
        if entries:
            self.con.executemany("UPDATE knowledge SET access_count=access_count+1, last_access_t=? WHERE id=?",
                                 [(self.now(), e["id"]) for e in entries])
            self.con.commit()

    def _hybrid(self, query, q, cols, filt, args) -> list[tuple]:
        """(row, relevance 0..1, cosine) for the CANDIDATES nearest entries by meaning and the
        CANDIDATES best FTS hits, relevance = their reciprocal-rank fusion scaled to the best."""
        self._refresh_vectors()
        vrows = self.con.execute(f"SELECT {cols}, v.vec FROM knowledge k JOIN knowledge_vec v ON v.id = k.id "
                                 f"WHERE 1=1{filt}", args).fetchall()
        if not vrows:
            return []
        mat = np.frombuffer(b"".join(r[-1] for r in vrows), dtype=np.float32).reshape(len(vrows), -1)
        cos = mat @ self.embed.query(query)
        by_id = {r[0]: (r[:-1], float(c)) for r, c in zip(vrows, cos)}
        rankings = [[vrows[i][0] for i in np.argsort(-cos, kind="stable")[:CANDIDATES]]]
        if q:
            rankings.append([kid for (kid,) in self.con.execute(
                f"SELECT k.id FROM knowledge_fts f JOIN knowledge k ON k.id = f.rowid WHERE knowledge_fts MATCH ?{filt} "
                f"ORDER BY bm25(knowledge_fts, 3.0, 1.0, 2.0, 2.0) LIMIT {CANDIDATES}", [q] + args)])
        fused = {}
        for ranking in rankings:
            for pos, kid in enumerate(ranking):
                if kid in by_id:                    # written after the refresh: not embedded yet
                    fused[kid] = fused.get(kid, 0.0) + 1.0 / (RRF_K + pos + 1)
        top = max(fused.values(), default=1.0)
        return [(by_id[kid][0], f / top, by_id[kid][1]) for kid, f in fused.items()]

    def _refresh_vectors(self):
        """Embed entries whose "topic: content" (or the model) changed since their vector."""
        have = dict(self.con.execute("SELECT id, hash FROM knowledge_vec"))
        todo = []
        for kid, topic, content in self.con.execute("SELECT id, topic, content FROM knowledge"):
            text = f"{topic}: {content}"
            h = hashlib.sha1(f"{self.embed.MODEL}\n{text}".encode()).hexdigest()
            if have.get(kid) != h:
                todo.append((kid, text, h))
        if not todo:
            return
        vecs = self.embed.passages([t for _, t, _ in todo])
        self.con.executemany("INSERT OR REPLACE INTO knowledge_vec(id, hash, vec) VALUES (?, ?, ?)",
                             [(kid, h, np.asarray(v, dtype=np.float32).tobytes())
                              for (kid, _, h), v in zip(todo, vecs)])
        self.con.commit()

    def brief(self, situation: dict, limit: int = 12) -> dict:
        """What to remember now. situation: {character: name, pos: [x, y, z], facet,
        mobiles: [names/labels], junctures: [kinds/summaries], intent: text, task: name}.
        Only entries for this character (for_character: untagged or its char: tag).
        `pinned`: every pinned entry, uncapped (the must-recall standing memories);
        `standing`: the other procedures/preferences of importance >= 7;
        `relevant`: what this situation calls up, and nothing else: entries located
        within NEAR_RADIUS, entries naming a mobile in view (its name as a phrase, not
        its title), and entries that mean what the intent, open junctures or task say
        (cosine >= BRIEF_MIN_SIM; word hits without an embedder). No entry is in two lists."""
        char = situation.get("character")
        pos, facet = situation.get("pos"), situation.get("facet")
        near = None if not pos else (facet or 0, pos[0], pos[1])
        names = sorted({n for n in (proper_name(m) for m in situation.get("mobiles") or ())
                        if len(n) >= 3 and words(n)})
        about = " ".join(str(v) for v in (*(situation.get("junctures") or ()), situation.get("intent") or "",
                                          situation.get("task") or "") if v)
        pinned = [e for e in self.pinned() if for_character(e, char)]
        seen = {e["id"] for e in pinned}
        standing = [e for e in self.search(None, kind=("preference", "procedure"), limit=1000, touch=False)
                    if e["importance"] >= 7 and e["x"] is None    # a located one comes up when there
                    and e["id"] not in seen and for_character(e, char)][:limit]
        seen |= {e["id"] for e in standing}
        cols = ", ".join("k." + c for c in COLS)
        cands = {}
        if near is not None:
            f, x, y = near
            for r in self.con.execute(
                    f"SELECT {cols} FROM knowledge k WHERE k.status='active' AND k.x IS NOT NULL "
                    "AND COALESCE(k.facet, 0)=? AND k.x BETWEEN ? AND ? AND k.y BETWEEN ? AND ?",
                    (f or 0, x - NEAR_RADIUS, x + NEAR_RADIUS, y - NEAR_RADIUS, y + NEAR_RADIUS)):
                cands[r[0]] = (r, 0.0, None)
        for n in names:              # the entry is about them (--entity), not a common word in some text
            phrase = 'entities : "' + n.replace('"', '""') + '"'
            for r in self.con.execute(f"SELECT {cols} FROM knowledge_fts f JOIN knowledge k ON k.id = f.rowid "
                                      "WHERE knowledge_fts MATCH ? AND k.status='active' LIMIT 50", (phrase,)):
                cands[r[0]] = (r, 1.0, None)
        found = {e["id"]: e for e in self._score(list(cands.values()), near)}
        if about.strip():
            for e in self.search(about, near=near, limit=limit, touch=False):
                if e["id"] not in found and (self.embed is None or (e.get("similarity") or 0) >= BRIEF_MIN_SIM):
                    found[e["id"]] = e
        relevant = sorted((e for e in found.values() if e["id"] not in seen and for_character(e, char)),
                          key=lambda e: (-e["score"], -e["id"]))[:limit]
        self._touch(relevant)
        return {"pinned": [_brief(e) for e in pinned], "relevant": [_brief(e) for e in relevant],
                "standing": [_brief(e) for e in standing], "character": char, "names": names,
                "query": about[:300], "near": near}

    def review(self, stale_days: float = 30.0, limit: int = 20) -> dict:
        """Maintenance: unconfirmed inferences, stale entries never recalled, and
        topics with several active facts (possible contradictions)."""
        now = self.now()
        rows = [_row(r) for r in self.con.execute(
            f"SELECT {', '.join(COLS)} FROM knowledge WHERE status='active'").fetchall()]
        inferred = [e for e in rows if e["source_type"] == "inferred" and e["confirmations"] == 0]
        stale = [e for e in rows if not e["access_count"] and now - e["updated_t"] > stale_days * 86400]
        by_topic = {}
        for e in rows:
            if e["kind"] == "fact":
                by_topic.setdefault(e["topic"].lower(), []).append(e)
        multi = {t: [_brief(e) for e in es] for t, es in by_topic.items() if len(es) > 1}
        return {"unconfirmed_inferences": [_brief(e) for e in inferred[:limit]],
                "stale": [_brief(e) for e in stale[:limit]], "topics_with_several_facts": multi}

    def stats(self) -> dict:
        by = {}
        for kind, status, n in self.con.execute("SELECT kind, status, COUNT(*) FROM knowledge GROUP BY 1, 2"):
            by.setdefault(kind, {})[status] = n
        return {"by_kind": by, "total": sum(sum(v.values()) for v in by.values())}


def _brief(e: dict) -> dict:
    out = {k: e[k] for k in ("id", "kind", "topic", "content", "confidence", "importance", "source_type")}
    if e.get("tags"):
        out["tags"] = e["tags"]
    if e.get("x") is not None:
        out["at"] = [e["facet"], e["x"], e["y"]]
    if "score" in e:
        out["score"] = e["score"]
    if "similarity" in e:
        out["similarity"] = e["similarity"]
    return out

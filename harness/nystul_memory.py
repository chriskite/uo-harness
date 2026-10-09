"""Memory-store changes Nystul proposes and the operator approves (docs/VISUALIZER.md §2.13).

Nystul stays read-only: his `uo_propose` tool (harness/nystul_tools.py) only validates a
proposal and reports it. harness/nystul.py stores the proposals of a finished answer, the
viz shows each with an Approve button, and only that click makes viz_server call apply()
on the memory store (harness/knowledge.py), posting the change to the Seer's chat as a
`system` `memory` row (it shows in the Seer panel, doesn't wake `ctl wait` and doesn't
touch the Seer's heartbeat).

A proposal (the uo_propose arguments):
  {"op": "add", "why", "kind", "topic", "content", "tags"?, "entities"?, "source"? (default
   "user"), "ref"?, "confidence"?, "importance"? (default 5), "supersedes"?}
  {"op": "update", "why", "id", "content"?, "topic"?, "tags"?, "importance"?, "confidence"?,
   "source"?, "ref"?}   a content/topic change makes a new version superseding #id
  {"op": "retract", "why", "id"}   why is the retraction reason
"""
import knowledge

OPS = ("add", "update", "retract")
WHY_MAX = 500
TOPIC_MAX = 200
CONTENT_MAX = 2000
REF_MAX = 500
LIST_MAX = 20


class ProposalError(ValueError):
    pass


def _text(p, key, limit, required=False):
    v = p.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise ProposalError(f"{key} is required")
        return None
    if not isinstance(v, str):
        raise ProposalError(f"{key} must be a string")
    v = v.strip()
    if len(v) > limit:
        raise ProposalError(f"{key} is over {limit} characters")
    return v


def _int(p, key, lo=None, hi=None, required=False):
    v = p.get(key)
    if v is None:
        if required:
            raise ProposalError(f"{key} is required")
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
        raise ProposalError(f"{key} must be an integer")
    v = int(v)
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ProposalError(f"{key} must be {lo}..{hi}")
    return v


def _strs(p, key):
    v = p.get(key)
    if v is None:
        return None
    if isinstance(v, str):
        v = [x for x in v.replace(",", " ").split() if x]
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v) or len(v) > LIST_MAX:
        raise ProposalError(f"{key} must be a list of at most {LIST_MAX} strings")
    return [x.strip() for x in v if x.strip()]


def normalize(p) -> dict:
    """The proposal with defaults applied and every field checked; ProposalError otherwise.
    Doesn't look at the store (targets() names the entries that must be active)."""
    if not isinstance(p, dict):
        raise ProposalError("a proposal is an object")
    op = p.get("op")
    if op not in OPS:
        raise ProposalError(f"op must be one of {', '.join(OPS)}")
    out = {"op": op, "why": _text(p, "why", WHY_MAX, required=True)}
    if op == "retract":
        out["id"] = _int(p, "id", 1, required=True)
        return out
    source = _text(p, "source", 20)
    if source is not None and source not in knowledge.SOURCES:
        raise ProposalError(f"source must be one of {', '.join(knowledge.SOURCES)}")
    conf = p.get("confidence")
    if conf is not None and (isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1):
        raise ProposalError("confidence must be 0..1")
    fields = {"topic": _text(p, "topic", TOPIC_MAX), "content": _text(p, "content", CONTENT_MAX),
              "tags": _strs(p, "tags"), "entities": _strs(p, "entities"), "ref": _text(p, "ref", REF_MAX),
              "confidence": None if conf is None else float(conf), "importance": _int(p, "importance", 1, 10)}
    if op == "add":
        kind = p.get("kind")
        if kind not in knowledge.KINDS:
            raise ProposalError(f"kind must be one of {', '.join(knowledge.KINDS)}")
        if not fields["topic"] or not fields["content"]:
            raise ProposalError("topic and content are required")
        out.update(kind=kind, **fields, source=source or "user", supersedes=_int(p, "supersedes", 1))
        out["tags"] = out["tags"] or []
        out["entities"] = out["entities"] or []
        out["importance"] = 5 if out["importance"] is None else out["importance"]
        return out
    out["id"] = _int(p, "id", 1, required=True)
    if all(fields[k] is None for k in ("topic", "content", "tags", "entities", "confidence", "importance")):
        raise ProposalError("an update changes at least one of content, topic, tags, entities, importance, "
                            "confidence")
    # the operator approves a new wording, so it is theirs unless the proposal says otherwise
    if source is None and (fields["content"] or fields["topic"]):
        source = "user"
    out.update(fields, source=source)
    return out


def targets(p: dict) -> list[int]:
    """Entries a normalized proposal changes: each must be active when applied."""
    return [i for i in (p.get("id"), p.get("supersedes")) if i is not None]


def compact(e: dict | None) -> dict | None:
    """A knowledge entry as a chat memory row and the approval card show it."""
    if e is None:
        return None
    out = {k: e.get(k) for k in ("id", "kind", "topic", "content", "confidence", "importance", "status",
                                 "source_type", "source_ref")}
    out["tags"] = list(e.get("tags") or [])
    return out


def apply(k: "knowledge.Knowledge", p: dict) -> tuple[dict, str, dict]:
    """Apply a normalized proposal. Returns (result {id, action}, chat text, chat data);
    knowledge.KnowledgeError when the store refuses it."""
    op = p["op"]
    if op == "add":
        out = k.add(p["kind"], p["topic"], p["content"], tags=p["tags"], entities=p["entities"],
                    source=p["source"], ref=p["ref"], confidence=p["confidence"], importance=p["importance"],
                    supersedes=p["supersedes"])
        verb = {"added": "inscribing", "confirmed": "confirmed", "superseded": "inscribing"}[out["action"]]
        text = (f"{verb} #{out['id']} {p['kind']} [{p['topic']}]: {p['content']}"
                + (f" (supersedes #{p['supersedes']})" if p["supersedes"] else ""))
        data = {"related": [compact(r) for r in out["related"]], "supersedes": p["supersedes"]}
    elif op == "update":
        out = k.update(p["id"], content=p["content"], topic=p["topic"], tags=p["tags"], entities=p["entities"],
                       confidence=p["confidence"], importance=p["importance"], source=p["source"], ref=p["ref"])
        new = out["id"] != p["id"]
        text = f"updated #{p['id']}" + (f" -> new version #{out['id']}" if new else "")
        data = {"related": [compact(r) for r in out.get("related", [])], "supersedes": p["id"] if new else None}
    else:
        out = k.retract(p["id"], p["why"])
        text = f"retracted #{p['id']}: {p['why']}"
        data = {}
    result = {"id": out["id"], "action": out["action"]}
    data = {**data, **result, "entry": compact(k.get(out["id"])), "why": p["why"]}
    return result, f"Nystul, approved by the operator: {text}", data

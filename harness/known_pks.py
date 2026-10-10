"""Known PKs: players fled on sight whatever their notoriety (user 2026-10-10, after the blue
gorilkaenjoyer killed Outland Dan at witcher_105; he had crowded Dan as a "thief" on 2026-10-07 too).

A player is a known PK once he attacked or killed one of our characters (the lumber runner marks him:
named in "<name> is attacking you!", or a player swinging at us), or when the user or the overseer
names one (`ctl pk add`). Kept in the memory store as job events of job "pk": `mark` {name, serial,
why, source} and `clear` {name, why}; the newest event per name wins. Names match by threats.pk_key
(case-insensitive, without the "Lord "/"Lady " title), serials exactly (a character's serial never changes:
gorilkaenjoyer was 0x00006A2E on both days). threats.Params.known_pks holds both (`keys`); threats.assess
makes such a player a `flee` at any distance in view, unless the server shows him green (an ally: user
2026-10-09).
"""
import threats

JOB = "pk"


def load(memory) -> dict[str, dict]:
    """{threats.pk_key(name): newest mark's data + t} over every character's pk events, cleared names dropped."""
    out = {}
    for e in memory.job_events(JOB):
        d = e["data"]
        key = threats.pk_key(d.get("name"))
        if not key:
            continue
        if e["kind"] == "mark":
            out[key] = {**d, "t": e["t"]}
        elif e["kind"] == "clear":
            out.pop(key, None)
    return out


def mark(memory, name: str, serial: int | None = None, why: str = "", source: str = "runner", **where) -> dict:
    """Record `name` (and his serial, when known) as a known PK; returns the event's data."""
    data = {"name": name.strip(), "serial": serial, "why": why, "source": source}
    memory.job_event(JOB, "mark", data, **where)
    return data


def clear(memory, name: str, why: str = "") -> None:
    memory.job_event(JOB, "clear", {"name": name.strip(), "why": why})


def keys(entries: dict[str, dict]) -> frozenset:
    """threats.Params.known_pks from load(): every lowercase name and every serial (int)."""
    return frozenset(entries) | frozenset(d["serial"] for d in entries.values() if d.get("serial") is not None)

"""Job analytics for the visualizer's Jobs page (docs/VISUALIZER.md §2.4).

  python harness/jobs.py [--db harness/data/harness.db] [--job lumber] [--since T]

`analytics(memory, job, since)` folds the memory store's trip rows
(`Memory.episodes(job)`), job events (`Memory.job_events(job, since)`: death,
theft, pk_seen, flee, ...), the `harvest_attempts` outcomes and, when present,
`harness/data/woods.json` (wood names and values) into one JSON-able dict:
per-trip rows, totals, per-day aggregates, a rolling logs/hr series and an
event timeline. `compute(...)` is the pure core over plain lists; it reads no
clock, so equal inputs give equal output.

Conventions
  - active time = the sum of trip durations (t_end - t_start); idle time between
    runs does not dilute logs/hr
  - a trip belongs to the day (at `utc_offset_s`) its t_start falls on; an event
    to the day of its t
  - deaths by cause: data.cause 'pk' | 'mob', anything else (or none) -> 'other'
  - theft loss: data.amount when numeric, else the item counts summed; data.items
    may be {name: n}, [{name|graphic, amount}] or [name, ...]
  - value: woods.json value_gp per log of that wood [INFERENCE: per unit; the
    economy research defines the unit]. A trip without a `woods` breakdown counts
    all its logs as DEFAULT_WOOD (the Shelter Island loop chops only ordinary
    trees). Logs of a wood without a known value are counted in
    `value_unpriced_logs`, never priced by guess; value is null when no log could
    be priced.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_WOODS = os.path.join(ROOT, "harness", "data", "woods.json")
DEFAULT_WOOD = "ordinary"
ROLLING_WINDOW_S = 3600.0
DEATH_CAUSES = ("pk", "mob", "other")
TIMELINE_KINDS = ("death", "theft", "pk_seen", "flee", "mob_attack", "resurrect")
HARVEST_OUTCOMES = ("success", "fail", "depleted", "unreachable", "not_tree")


# ------------------------------------------------------------------ inputs
def load_woods(path: str = DEFAULT_WOODS) -> dict | None:
    """woods.json as {name: entry}, or None when absent or unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    woods = doc.get("woods") if isinstance(doc, dict) else None
    if not isinstance(woods, list):
        return None
    out = {}
    for w in woods:
        if isinstance(w, dict) and isinstance(w.get("name"), str):
            out[w["name"]] = w
    return out


def wood_value(woods: dict | None, name: str):
    """value_gp of a wood, or None when unknown (no file, no entry, null, not a number)."""
    v = (woods or {}).get(name, {}).get("value_gp")
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _num(v, default=0):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _rate(n, seconds):
    return round(n * 3600.0 / seconds, 2) if seconds and seconds > 0 else None


def _ratio(n, d):
    return round(n / d, 2) if d else None


def _day(t: float, utc_offset_s: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t + utc_offset_s))


def theft_loss(data: dict) -> tuple[int, dict]:
    """(amount, {item name: count}) of one theft event's data."""
    items = {}
    raw = data.get("items")
    if isinstance(raw, dict):
        for k, v in raw.items():
            items[str(k)] = items.get(str(k), 0) + int(_num(v, 1))
    elif isinstance(raw, list):
        for it in raw:
            if isinstance(it, dict):
                name = str(it.get("name") or it.get("graphic") or "?")
                items[name] = items.get(name, 0) + int(_num(it.get("amount"), 1))
            else:
                items[str(it)] = items.get(str(it), 0) + 1
    amount = data.get("amount")
    amount = int(amount) if isinstance(amount, (int, float)) and not isinstance(amount, bool) else sum(items.values())
    return amount, items


# ------------------------------------------------------------------ folding
def _blank():
    return {"trips": 0, "logs": 0, "stored": 0, "active_s": 0.0, "captchas": 0, "captcha_wait_s": 0.0,
            "attempts": 0, "successes": 0, "woods": {},
            "deaths": {c: 0 for c in DEATH_CAUSES}, "thefts": {"count": 0, "amount": 0, "items": {}},
            "pk_seen": 0, "flees": 0, "value_gp": None, "value_unpriced_logs": 0}


def _add_trip(agg, trip):
    agg["trips"] += 1
    agg["logs"] += trip["logs"]
    agg["stored"] += trip["stored"]
    agg["active_s"] += trip["duration_s"] or 0.0
    agg["captchas"] += trip["captchas"]
    agg["captcha_wait_s"] += trip["captcha_wait_s"]
    agg["attempts"] += trip["attempts"]
    agg["successes"] += trip["successes"]
    for name, n in trip["woods"].items():
        agg["woods"][name] = agg["woods"].get(name, 0) + n
    if trip["value_gp"] is not None:
        agg["value_gp"] = round((agg["value_gp"] or 0) + trip["value_gp"], 2)
    agg["value_unpriced_logs"] += trip["value_unpriced_logs"]


def _add_event(agg, ev):
    kind, data = ev["kind"], ev["data"]
    if kind == "death":
        cause = data.get("cause")
        agg["deaths"][cause if cause in ("pk", "mob") else "other"] += 1
    elif kind == "theft":
        amount, items = theft_loss(data)
        th = agg["thefts"]
        th["count"] += 1
        th["amount"] += amount
        for k, v in items.items():
            th["items"][k] = th["items"].get(k, 0) + v
    elif kind == "pk_seen":
        agg["pk_seen"] += 1
    elif kind == "flee":
        agg["flees"] += 1


def _finish(agg):
    agg["active_s"] = round(agg["active_s"], 1)
    agg["captcha_wait_s"] = round(agg["captcha_wait_s"], 1)
    agg["active_hours"] = round(agg["active_s"] / 3600.0, 3)
    agg["logs_per_hour"] = _rate(agg["logs"], agg["active_s"])
    agg["logs_per_trip"] = _ratio(agg["logs"], agg["trips"])
    agg["success_rate"] = _ratio(agg["successes"], agg["attempts"])
    agg["deaths"]["total"] = sum(agg["deaths"][c] for c in DEATH_CAUSES)
    return agg


def _trip_row(i, row, woods):
    t0, t1 = row.get("t_start"), row.get("t_end")
    duration = round(t1 - t0, 1) if _num(t0, None) is not None and _num(t1, None) is not None else None
    logs = int(_num(row.get("logs")))
    breakdown = {str(k): int(_num(v)) for k, v in row["woods"].items()} if isinstance(row.get("woods"), dict) else {}
    priced = breakdown or ({DEFAULT_WOOD: logs} if logs else {})
    value, unpriced = None, 0
    for name, n in priced.items():
        v = wood_value(woods, name)
        if v is None:
            unpriced += n
        else:
            value = round((value or 0) + v * n, 2)
    phases = row.get("phases_s") if isinstance(row.get("phases_s"), dict) else {}
    return {"n": i + 1, "trip": row.get("trip"), "venue": row.get("venue"), "t_start": t0, "t_end": t1,
            "duration_s": duration, "logs": logs, "stored": int(_num(row.get("stored"))),
            "logs_per_hour": _rate(logs, duration),
            "captchas": int(_num(row.get("captchas"))), "captcha_wait_s": round(_num(row.get("captcha_wait_s")), 1),
            "attempts": int(_num(row.get("attempts"))), "successes": int(_num(row.get("successes"))),
            "phases_s": {k: v for k, v in phases.items() if _num(v, None) is not None},
            "steps": row.get("steps"), "blocked": row.get("blocked"),
            "woods": breakdown, "value_gp": value, "value_unpriced_logs": unpriced,
            "events": {}}


def rolling_series(trips: list[dict], window_s: float = ROLLING_WINDOW_S) -> list[dict]:
    """One point per timed trip, at its t_end: logs/hr over the trips that ended
    within the last `window_s` (active time only), and cumulative logs/hr."""
    timed = sorted((t for t in trips if t["duration_s"] and t["t_end"] is not None), key=lambda t: t["t_end"])
    out, cum_logs, cum_s = [], 0, 0.0
    for i, t in enumerate(timed):
        cum_logs += t["logs"]
        cum_s += t["duration_s"]
        win = [u for u in timed[:i + 1] if u["t_end"] > t["t_end"] - window_s]
        out.append({"t": t["t_end"], "n": t["n"],
                    "logs_per_hour": _rate(sum(u["logs"] for u in win), sum(u["duration_s"] for u in win)),
                    "cum_logs_per_hour": _rate(cum_logs, cum_s), "window_trips": len(win)})
    return out


def compute(episodes: list[dict], events: list[dict], attempts: list[tuple] | None = None,
            woods: dict | None = None, job: str = "lumber", since: float = 0.0,
            utc_offset_s: int = 0, window_s: float = ROLLING_WINDOW_S) -> dict:
    """Pure core. episodes: trip row dicts; events: job_events dicts
    ({t, kind, data, facet, x, y}); attempts: harvest_attempts rows
    (t, facet, x, y, z, outcome, amount)."""
    since = float(since)
    rows = [r for r in episodes if isinstance(r, dict) and _num(r.get("t_start"), 0) >= since]
    rows.sort(key=lambda r: _num(r.get("t_start"), 0))
    trips = [_trip_row(i, r, woods) for i, r in enumerate(rows)]
    evs = sorted(({**e, "data": e.get("data") if isinstance(e.get("data"), dict) else {}}
                  for e in events if _num(e.get("t"), 0) >= since),
                 key=lambda e: (e["t"], e.get("id") or 0))

    totals, days = _blank(), {}
    for trip in trips:
        _add_trip(totals, trip)
        if trip["t_start"] is not None:
            _add_trip(days.setdefault(_day(trip["t_start"], utc_offset_s), _blank()), trip)
    for e in evs:
        _add_event(totals, e)
        _add_event(days.setdefault(_day(e["t"], utc_offset_s), _blank()), e)
        for trip in trips:
            if trip["t_start"] is not None and trip["t_end"] is not None and trip["t_start"] <= e["t"] <= trip["t_end"]:
                trip["events"][e["kind"]] = trip["events"].get(e["kind"], 0) + 1
                break
    _finish(totals)
    stamps = [t for trip in trips for t in (trip["t_start"], trip["t_end"]) if t is not None] + [e["t"] for e in evs]
    totals["first_t"] = min(stamps) if stamps else None
    totals["last_t"] = max(stamps) if stamps else None

    harvest = None
    if attempts is not None:
        harvest = {o: 0 for o in HARVEST_OUTCOMES}
        harvest["yield"] = 0
        for row in attempts:
            t, outcome, amount = row[0], row[5], row[6]
            if t < since:
                continue
            harvest[outcome] = harvest.get(outcome, 0) + 1
            harvest["yield"] += amount or 0
        tried = harvest["success"] + harvest["fail"]
        harvest["success_rate"] = _ratio(harvest["success"], tried)

    names = sorted(set(totals["woods"]) | set(woods or {}))
    wood_rows = []
    for name in names:
        entry = (woods or {}).get(name, {})
        n = totals["woods"].get(name, 0)
        v = wood_value(woods, name)
        if n or entry:
            wood_rows.append({"name": name, "logs": n, "value_gp": v,
                              "total_gp": round(v * n, 2) if v is not None else None,
                              "min_skill": entry.get("min_skill"), "known": bool(entry)})
    return {
        "job": job, "since": since, "utc_offset_s": utc_offset_s, "window_s": window_s,
        "woods_file": woods is not None,
        "trips": trips,
        "totals": totals,
        "days": [{"day": d, **_finish(a)} for d, a in sorted(days.items())],
        "rolling": rolling_series(trips, window_s),
        "events": [{"id": e.get("id"), "t": e["t"], "kind": e["kind"], "facet": e.get("facet"),
                    "x": e.get("x"), "y": e.get("y"), "data": e["data"]} for e in evs],
        "woods": wood_rows,
        "harvest": harvest,
    }


def harvest_rows(memory, since: float = 0.0) -> list[tuple]:
    return memory.con.execute("SELECT t, facet, x, y, z, outcome, amount FROM harvest_attempts "
                              "WHERE t >= ? ORDER BY t", (since,)).fetchall()


# ------------------------------------------------------------------ hunt
GOLD_GRAPHIC = 0x0EED
HUNT_PER_KILL_KINDS = ("kill", "loot")   # folded into visits and monsters, not the timeline


def loot_xp(data: dict):
    """Mastery-chain XP of one looted kill, or None when unknown. data.xp (the gold the
    corpse held before looting; loop_hunt records it since 2026-10-02), else the gold
    piles taken (older loot rows), else None."""
    xp = data.get("xp")
    if _num(xp, None) is not None:
        return int(xp)
    items = data.get("items")
    if not isinstance(items, list):
        return None
    total = 0
    for it in items:
        g = it.get("graphic") if isinstance(it, dict) else None
        try:
            gold = int(g, 16) == GOLD_GRAPHIC if isinstance(g, str) else g == GOLD_GRAPHIC
        except ValueError:
            gold = False
        if gold:
            total += int(_num(it.get("amount"), 1))
    return total


def _hunt_blank():
    return {"visits": 0, "active_s": 0.0, "kills": 0, "gold": 0, "xp": 0, "xp_kills": 0, "looted": 0,
            "hits_lost": 0, "casts": 0, "heals": 0, "potions": 0, "leaves": 0,
            "speech_holds": 0, "speech_wait_s": 0.0, "deaths": {c: 0 for c in DEATH_CAUSES}}


def _hunt_add_visit(agg, v):
    agg["visits"] += 1
    agg["active_s"] += v["duration_s"] or 0.0
    for k in ("hits_lost", "casts", "heals", "potions"):
        agg[k] += v[k]


def _hunt_add_event(agg, e):
    kind, data = e["kind"], e["data"]
    if kind == "kill":
        agg["kills"] += 1
    elif kind == "loot":
        agg["looted"] += 1
        agg["gold"] += int(_num(data.get("gold")))
        xp = loot_xp(data)
        if xp is not None:
            agg["xp"] += xp
            agg["xp_kills"] += 1
    elif kind == "leave":
        agg["leaves"] += 1
    elif kind == "speech_hold":
        agg["speech_holds"] += 1
    elif kind == "speech_clear":
        agg["speech_wait_s"] += _num(data.get("waited_s"), 0.0)
    elif kind == "death":
        cause = data.get("cause")
        agg["deaths"][cause if cause in ("pk", "mob") else "other"] += 1


def _hunt_finish(agg):
    agg["active_s"] = round(agg["active_s"], 1)
    agg["speech_wait_s"] = round(agg["speech_wait_s"], 1)
    agg["active_hours"] = round(agg["active_s"] / 3600.0, 3)
    agg["xp_unknown_kills"] = max(0, agg["kills"] - agg["xp_kills"])
    agg["gold_per_kill"] = _ratio(agg["gold"], agg["looted"])
    agg["xp_per_kill"] = _ratio(agg["xp"], agg["xp_kills"])
    agg["deaths"]["total"] = sum(agg["deaths"][c] for c in DEATH_CAUSES)
    return agg


def _visit_row(i, row):
    t0, t1 = row.get("t_start"), row.get("t_end")
    duration = round(t1 - t0, 1) if _num(t0, None) is not None and _num(t1, None) is not None else None
    return {"n": i + 1, "visit": row.get("visit"), "t_start": t0, "t_end": t1, "duration_s": duration,
            "spot": row.get("spot"), "spell": row.get("spell"), "ended": row.get("ended"),
            "kills": 0, "gold": 0, "xp": 0, "xp_kills": 0, "looted": 0,
            "hits_lost": int(_num(row.get("hits_lost"))), "casts": int(_num(row.get("casts"))),
            "heals": int(_num(row.get("heals"))), "potions": int(_num(row.get("potions"))),
            "events": {}}


def _visit_rates(v):
    d = v["duration_s"]
    v["kills_per_hour"] = _rate(v["kills"], d)
    v["gold_per_hour"] = _rate(v["gold"], d)
    v["xp_per_hour"] = _rate(v["xp"], d)


def hunt_rolling(visits: list[dict], window_s: float = ROLLING_WINDOW_S) -> list[dict]:
    """One point per timed visit, at its t_end: kills/gold/xp per hour over the visits
    that ended within the last `window_s` (active time only), and cumulative."""
    timed = sorted((v for v in visits if v["duration_s"] and v["t_end"] is not None), key=lambda v: v["t_end"])
    out, cum, cum_s = [], {"kills": 0, "gold": 0, "xp": 0}, 0.0
    for i, v in enumerate(timed):
        cum_s += v["duration_s"]
        win = [u for u in timed[:i + 1] if u["t_end"] > v["t_end"] - window_s]
        win_s = sum(u["duration_s"] for u in win)
        p = {"t": v["t_end"], "n": v["n"], "window_visits": len(win)}
        for k in cum:
            cum[k] += v[k]
            p[f"{k}_per_hour"] = _rate(sum(u[k] for u in win), win_s)
            p[f"cum_{k}_per_hour"] = _rate(cum[k], cum_s)
        out.append(p)
    return out


def compute_hunt(episodes: list[dict], events: list[dict], since: float = 0.0, utc_offset_s: int = 0,
                 window_s: float = ROLLING_WINDOW_S) -> dict:
    """Pure core of the hunt dashboard. episodes: loop_hunt visit rows; events:
    job_events dicts of the hunt job.

    Kills, gold and XP come from the kill/loot events (written as they happen, so a
    run that ends without its visit row still counts); a visit's own figures are the
    events inside its [t_start, t_end]. Rates are the in-visit figures over active
    time (the sum of visit durations). XP: loot_xp(); a kill that was never looted has
    unknown XP (xp_unknown_kills), never guessed. A loot counts for the monster its
    data.name names, else the kill whose serial is data.mob, else (loot rows before
    2026-10-02 carry neither) the latest earlier kill no loot claimed yet."""
    since = float(since)
    rows = [r for r in episodes if isinstance(r, dict) and _num(r.get("t_start"), 0) >= since]
    rows.sort(key=lambda r: _num(r.get("t_start"), 0))
    visits = [_visit_row(i, r) for i, r in enumerate(rows)]
    evs = sorted(({**e, "data": e.get("data") if isinstance(e.get("data"), dict) else {}}
                  for e in events if _num(e.get("t"), 0) >= since),
                 key=lambda e: (e["t"], e.get("id") or 0))

    totals, days, monsters = _hunt_blank(), {}, {}
    for v in visits:
        _hunt_add_visit(totals, v)
        if v["t_start"] is not None:
            _hunt_add_visit(days.setdefault(_day(v["t_start"], utc_offset_s), _hunt_blank()), v)
    in_visits = {"kills": 0, "gold": 0, "xp": 0}
    unclaimed = []                           # (serial, name) of kills no loot claimed yet
    for e in evs:
        _hunt_add_event(totals, e)
        _hunt_add_event(days.setdefault(_day(e["t"], utc_offset_s), _hunt_blank()), e)
        kind, data = e["kind"], e["data"]
        if kind == "kill":
            name = str(data.get("name") or "unknown")
            unclaimed.append((data.get("serial"), name))
            monsters.setdefault(name, {"kills": 0, "looted": 0, "gold": 0, "xp": 0, "xp_kills": 0})["kills"] += 1
        elif kind == "loot":
            mob, name = data.get("mob"), data.get("name")
            if mob is not None:
                claim = next((i for i in range(len(unclaimed) - 1, -1, -1) if unclaimed[i][0] == mob), None)
            else:
                claim = len(unclaimed) - 1 if unclaimed and not name else None
            if claim is not None:
                claimed = unclaimed.pop(claim)[1]
                name = name or claimed
            m = monsters.setdefault(str(name or "unknown"), {"kills": 0, "looted": 0, "gold": 0, "xp": 0, "xp_kills": 0})
            m["looted"] += 1
            m["gold"] += int(_num(data.get("gold")))
            xp = loot_xp(data)
            if xp is not None:
                m["xp"] += xp
                m["xp_kills"] += 1
        for v in visits:
            if v["t_start"] is None or v["t_end"] is None or not v["t_start"] <= e["t"] <= v["t_end"]:
                continue
            if kind == "kill":
                v["kills"] += 1
                in_visits["kills"] += 1
            elif kind == "loot":
                v["looted"] += 1
                v["gold"] += int(_num(data.get("gold")))
                in_visits["gold"] += int(_num(data.get("gold")))
                xp = loot_xp(data)
                if xp is not None:
                    v["xp"] += xp
                    v["xp_kills"] += 1
                    in_visits["xp"] += xp
            else:
                v["events"][kind] = v["events"].get(kind, 0) + 1
            break
    for v in visits:
        _visit_rates(v)
    _hunt_finish(totals)
    totals["outside_visits"] = {k: totals[k] - in_visits[k] for k in in_visits}
    for k in in_visits:
        totals[f"{k}_per_hour"] = _rate(in_visits[k], totals["active_s"])
    stamps = [t for v in visits for t in (v["t_start"], v["t_end"]) if t is not None] + [e["t"] for e in evs]
    totals["first_t"] = min(stamps) if stamps else None
    totals["last_t"] = max(stamps) if stamps else None

    day_rows = []
    for d, a in sorted(days.items()):
        _hunt_finish(a)
        day_rows.append({"day": d, **a})
    monster_rows = [{"name": name, **m, "gold_per_kill": _ratio(m["gold"], m["looted"]),
                     "xp_per_kill": _ratio(m["xp"], m["xp_kills"])}
                    for name, m in sorted(monsters.items(), key=lambda kv: (-kv[1]["kills"], kv[0]))]
    return {
        "job": "hunt", "since": since, "utc_offset_s": utc_offset_s, "window_s": window_s,
        "visits": visits,
        "totals": totals,
        "days": day_rows,
        "rolling": hunt_rolling(visits, window_s),
        "monsters": monster_rows,
        "events": [{"id": e.get("id"), "t": e["t"], "kind": e["kind"], "facet": e.get("facet"),
                    "x": e.get("x"), "y": e.get("y"), "data": e["data"]}
                   for e in evs if e["kind"] not in HUNT_PER_KILL_KINDS],
    }


_DEFAULT = object()


def analytics(memory, job: str = "lumber", since: float = 0.0, woods=_DEFAULT,
              utc_offset_s: int = 0, window_s: float = ROLLING_WINDOW_S) -> dict:
    """Analytics from a harness.memory.Memory, or empty ones for memory None (no store
    yet). `hunt` gets compute_hunt(); every other job the trip analytics. `woods`: a
    load_woods() dict, None for no values, or default = harness/data/woods.json if
    present. Harvest outcomes come only for the lumber job (harvest_attempts has no
    job column)."""
    episodes = memory.episodes(job) if memory is not None else []
    events = memory.job_events(job, since) if memory is not None else []
    if job == "hunt":
        return compute_hunt(episodes, events, since, utc_offset_s, window_s)
    if woods is _DEFAULT:
        woods = load_woods()
    attempts = harvest_rows(memory, since) if memory is not None and job == "lumber" else None
    return compute(episodes, events, attempts, woods, job, since, utc_offset_s, window_s)


def main(argv=None):
    import memory as memory_mod
    ap = argparse.ArgumentParser(description="job analytics from the harness memory store")
    ap.add_argument("--db", default=memory_mod.DEFAULT_DB)
    ap.add_argument("--job", default="lumber")
    ap.add_argument("--since", type=float, default=0.0)
    a = ap.parse_args(argv)
    m = memory_mod.Memory(a.db)
    try:
        out = analytics(m, a.job, a.since, utc_offset_s=time.localtime().tm_gmtoff)
    finally:
        m.close()
    keys = ("totals", "days", "monsters") if a.job == "hunt" else ("totals", "days", "harvest")
    print(json.dumps({k: out[k] for k in keys}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

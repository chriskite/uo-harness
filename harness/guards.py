"""Town-guard zones: where to run when a PK shows up and recall fails.

Guard zone boundaries are not in the client data. Two sources:
- learned entries: the server's "You are now under the protection of the town
  guards." (cliloc 500112) right after a one-tile step marks the tile we stand on
  as inside a zone (guard_points table, memory.py). Mover.step records them
  live; `backfill` replays the store's event log.
- bank markers from the client's Banks_and_Healers.xml (read-only, install dir),
  minus lawless towns.

The server's guard notices lag and skip crossings (docs/NOTES.md "Guard zone
notices", live 2026-10-02 Prevalia: one 500113 3.3 s after the step out, then
none over 14 more crossings in 95 s). So a notice only says which side we are on
when it arrives: a "left" notice (500113) doesn't tell where the inside is, and
no tile beyond the one we stand on is inferred.

CLI: python harness/guards.py backfill [--db PATH]
"""
import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nav  # noqa: E402
import uomap  # noqa: E402

ENTER_CLILOC = 500112          # "You are now under the protection of the town guards."
MARKERS = os.path.join(uomap.INSTALL, "ClassicUO", "Data", "Client", "Banks_and_Healers.xml")
LAWLESS_TOWNS = ("Corpse Creek",)
FLEE_MAX_DIST = 250            # tiles (Chebyshev): farther guarded places aren't a flight
ATTACKER_NEAR = 12             # an attacker this close steers the flight away from it
BANK_RADIUS = 2
ENTRY_WINDOW_S = 1.0           # backfill: the notice follows its step within this (no recall/teleport since)


def entries(events) -> list:
    """[(x, y)] tiles we stood on when an enter notice (500112) came, for each
    one in `events` (event dicts in order) that follows a one-tile `step`."""
    out = []
    last = None
    for e in events:
        ev = e.get("ev")
        if ev == "step":
            last = (tuple(e["from"][:2]), tuple(e["to"][:2]))
        elif ev == "cliloc" and e.get("cliloc") == ENTER_CLILOC and last is not None \
                and nav.chebyshev(last[0], last[1]) == 1:
            out.append(last[1])
    return out


def backfill(memory) -> int:
    """Record a guard point for every enter notice in the store's event log that
    came within ENTRY_WINDOW_S of a one-tile step: that step's destination. Facet
    0: step events carry no facet, and every session so far was on map 0.
    Returns the count."""
    n = 0
    session = None
    last = None                  # (from, to, t) of the session's last step
    rows = memory.con.execute("SELECT session, seq, t, ev, data FROM events "
                              "WHERE ev IN ('step', 'cliloc') ORDER BY session, seq").fetchall()
    for sess, _seq, t, ev, data in rows:
        if sess != session:
            session, last = sess, None
        e = json.loads(data)
        if ev == "step":
            last = (tuple(e["from"][:2]), tuple(e["to"][:2]), t)
        elif e.get("cliloc") == ENTER_CLILOC and last is not None \
                and nav.chebyshev(last[0], last[1]) == 1 and t is not None and last[2] is not None \
                and 0 <= t - last[2] <= ENTRY_WINDOW_S:
            memory.guard_point_record(0, last[1][0], last[1][1], t)
            n += 1
    return n


def bank_markers(path: str = MARKERS) -> list:
    """[((x, y), facet)] of the client's BANK markers outside lawless towns;
    [] if the file is missing or unreadable."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return []
    out = []
    for m in root.iter("Marker"):
        name = m.get("Name") or ""
        if m.get("Icon") != "BANK" or name.startswith(LAWLESS_TOWNS):
            continue
        try:
            out.append(((int(m.get("X")), int(m.get("Y"))), int(m.get("Facet") or 0)))
        except (TypeError, ValueError):
            continue
    return out


def flee_goals(points, banks, facet, me, attacker) -> list:
    """[((x, y), radius)] guarded places within FLEE_MAX_DIST of `me`: learned
    points (radius 0) and bank markers on `facet` (BANK_RADIUS). With an attacker
    within ATTACKER_NEAR, places nearer to it than to us are dropped (unless
    that drops all of them)."""
    me = tuple(me[:2])
    cands = [((int(p[0]), int(p[1])), 0) for p in points]
    cands += [(tuple(c), BANK_RADIUS) for c, f in banks if (f or 0) == (facet or 0)]
    cands = [(c, r) for c, r in cands if nav.chebyshev(c, me) <= FLEE_MAX_DIST]
    if attacker is not None and nav.chebyshev(me, tuple(attacker[:2])) <= ATTACKER_NEAR:
        a = tuple(attacker[:2])
        away = [(c, r) for c, r in cands if nav.chebyshev(c, a) >= nav.chebyshev(c, me)]
        if away:
            cands = away
    return cands


def main():
    import memory as memory_mod
    ap = argparse.ArgumentParser(description="town-guard zone points")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill", help="record guard points from the store's event log")
    b.add_argument("--db", default=memory_mod.DEFAULT_DB)
    args = ap.parse_args()
    mem = memory_mod.Memory(args.db)
    n = backfill(mem)
    total = mem.con.execute("SELECT COUNT(*) FROM guard_points").fetchone()[0]
    print(json.dumps({"recorded": n, "points": total}))


if __name__ == "__main__":
    main()

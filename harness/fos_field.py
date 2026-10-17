"""The Field of Souls field map: where the event's monsters come into view, spawn and die, and how long
we watched each part of the field, for the viz's reaper heatmap (docs/VISUALIZER.md §2.17; user request
2026-10-17: "map the reaper spawns ... an overlay on the map in the viz UI, like reaper spawn heatmap").

The field (live 2026-10-17, Logan Wolf, captures 20261017_155426 Normal and 20261017_172358 Hard, map0): an
island around (6295, 700). The Hard instance is the same island shifted by HARD_OFFSET (all 2,350 land tiles
sampled on a 3-tile grid over the island equal at that shift, none at the neighbouring ones). So both
instances share one field frame: the Normal one's coordinates.

What the proxy records (FieldLog, one per live session; proxy events, so they land in the store's events):
- `fos_session` {instance, spirit, x, y}: the server named our spirit on entering ("If you put the spirit of
  ..."), the start of a 15-minute field session.
- `fos_mob` {name, serial, x, y, z, dist, spawn}: a field monster (FOS_NAMES) first identified. The stock
  client single-clicks every mobile as it comes into view and the server's click label names it (soul_jar
  SpiritWatch), so its position then is where it came into view. Of 102 reapers first seen in the two runs,
  63 were exactly VIEW_R tiles off: they walked into view at its edge. One first seen within SPAWN_R tiles
  appeared inside the view: `spawn` true, unless we had just arrived (ARRIVAL_S after a jump of our own
  position: entering the field fills the whole view at once). Reapers have human bodies (400/401) like
  players and eerie spirits, so the label is the only way to tell them.
- `fos_corpse` {name, serial, corpse, x, y, z, in_view}: a field monster's corpse (Outlands 0xFF 0xDEAD),
  where it died. `in_view`: it died in our view (the 0xAF that comes first prunes it as dead); otherwise the
  corpse came into view later. Per run about 170 reaper corpses came into view against ~50 live reapers seen.

reaper_map() turns them into the overlay's cells: for each CELL x CELL square of the field frame, how long
we watched it (our steps there, within SPAWN_R for spawns and VIEW_R for deaths), and the spawns and in-view
deaths in it. Rates per hour watched, not raw counts: raw counts only show where we went.

Backfill: `python harness/fos_field.py backfill` replays the captures of sessions that entered the field
before the proxy recorded these events and stores what FieldLog would have emitted (events seq
BACKFILL_SEQ0 + i, never reached by a live session).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

NORMAL_BOX = (6200, 600, 6396, 804)          # the island in the field frame: x0, y0, x1, y1 (exclusive)
HARD_OFFSET = (874, -32)
INSTANCES = (("normal", (0, 0)), ("hard", HARD_OFFSET))
FACET = 0
FOS_NAMES = ("a reaper of souls", "a haunted hayman", "a roaming terror", "a roaming nightmare")
REAPER = "a reaper of souls"
VIEW_R = 18          # mobiles come into view this far off (63 of 102 first sightings)
SPAWN_R = 14         # first seen this close: it appeared inside the view
ARRIVAL_S = 3.0      # first sightings this soon after a jump of ours are the view filling in, not spawns
JUMP_TILES = 8       # our position moving this far between two looks is a jump (entering, a teleport)
FIELD_S = 16 * 60.0  # a field session lasts 15 minutes
CELL = 4
WATCH_GAP_S = 120.0  # time between two of our steps counts as watching for at most this long (pets fight, we stand)
DIED_S = 2.0         # a corpse's 0xDEAD this soon after its mobile was pruned as dead: it died in view
BACKFILL_SEQ0 = 1_000_000_000
EVENT_START = datetime.datetime(2026, 10, 17).timestamp()   # local midnight of the event's first day
SYSTEM_SERIAL = 0xFFFFFFFF
MOBILE_SERIAL_MAX = 0x3FFFFFFF
_NAMED = re.compile(r"^If you put the spirit of (.+?) to rest, you will receive")


def cheb(ax, ay, bx, by) -> int:
    return max(abs(ax - bx), abs(ay - by))


def to_field(x, y):
    """(instance name, field-frame x, y) of an absolute map0 tile inside a field instance, else None."""
    if x is None or y is None:
        return None
    x0, y0, x1, y1 = NORMAL_BOX
    for name, (dx, dy) in INSTANCES:
        fx, fy = x - dx, y - dy
        if x0 <= fx < x1 and y0 <= fy < y1:
            return name, fx, fy
    return None


def fos_name(text: str | None) -> str | None:
    """The field monster a label or corpse name is about ("a reaper of souls corpse" -> "a reaper of souls")."""
    t = (text or "").lower()
    return next((n for n in FOS_NAMES if t == n or t.startswith(n + " ") or t.startswith(n + "'")), None)


class FieldLog:
    """One session's field watcher (module docstring): feed() returns the proxy events to emit."""

    def __init__(self):
        self.mobs: set[int] = set()
        self.corpses: set[int] = set()
        self.last = None             # our (x, y, map) at the previous look
        self.arrived_t = None        # when our position last jumped
        self.died: dict[int, float] = {}   # serial -> when it was pruned as dead (0xAF: died in view)

    def feed(self, events, now: float, state) -> list[dict]:
        out = []
        me = state.self
        if me.x is not None:
            here = (me.x, me.y, me.map)
            if self.last is None or here[2] != self.last[2] or cheb(*here[:2], *self.last[:2]) > JUMP_TILES:
                self.arrived_t = now
            self.last = here
        for e in events:
            ev = e.get("ev")
            if ev == "prune" and e.get("why") == "dead":
                self.died[e["serial"]] = now        # 0xAF: it died in view; its 0xDEAD comes a packet or two later
            elif ev == "speech_heard":
                if e.get("serial") == SYSTEM_SERIAL:
                    if (m := _NAMED.match(e.get("text") or "")) and (f := to_field(me.x, me.y)):
                        out.append({"ev": "fos_session", "instance": f[0], "spirit": m.group(1), "x": me.x, "y": me.y})
                    continue
                s = e.get("serial") or 0
                name = fos_name(e.get("text")) if e.get("type") == 6 else None
                mob = state.mobiles.get(s)
                if name is None or not 0 < s <= MOBILE_SERIAL_MAX or s in self.mobs or mob is None \
                        or me.map != FACET or to_field(mob.x, mob.y) is None:
                    continue
                self.mobs.add(s)
                dist = cheb(mob.x, mob.y, me.x, me.y)
                settled = self.arrived_t is None or now - self.arrived_t > ARRIVAL_S
                out.append({"ev": "fos_mob", "name": name, "serial": f"0x{s:08X}", "x": mob.x, "y": mob.y,
                            "z": mob.z, "dist": dist, "spawn": dist <= SPAWN_R and settled})
            elif ev == "mobile_death":
                name, c = fos_name(e.get("name")), e.get("corpse")
                it = state.items.get(c) if c is not None else None
                if name is None or c in self.corpses or it is None or me.map != FACET or to_field(it.x, it.y) is None:
                    continue
                self.corpses.add(c)
                in_view = now - self.died.pop(e["serial"], -1e9) <= DIED_S
                out.append({"ev": "fos_corpse", "name": name, "serial": f"0x{e['serial']:08X}",
                            "corpse": f"0x{c:08X}", "x": it.x, "y": it.y, "z": it.z, "in_view": in_view})
        if len(self.died) > 256:
            self.died = {s: t for s, t in self.died.items() if now - t <= DIED_S}
        return out


# --------------------------------------------------------------------------- the map
def _cell_of(fx, fy):
    x0, y0, _, _ = NORMAL_BOX
    return (fx - x0) // CELL, (fy - y0) // CELL


def _coverage(dwell, r: int):
    """Seconds watched per cell: the dwell (seconds per field-frame tile, numpy [y, x]) of every tile within r
    tiles (Chebyshev) of the cell's centre, by a summed-area table."""
    import numpy as np
    h, w = dwell.shape
    sat = np.zeros((h + 1, w + 1))
    sat[1:, 1:] = dwell.cumsum(0).cumsum(1)
    centres_x = np.arange(0, w, CELL) + CELL // 2
    centres_y = np.arange(0, h, CELL) + CELL // 2
    xa, xb = np.clip(centres_x - r, 0, w), np.clip(centres_x + r + 1, 0, w)
    ya, yb = np.clip(centres_y - r, 0, h), np.clip(centres_y + r + 1, 0, h)
    return (sat[yb][:, xb] - sat[ya][:, xb] - sat[yb][:, xa] + sat[ya][:, xa])   # [cell y, cell x]


def reaper_map(con, name: str = REAPER, now: float | None = None) -> dict:
    """The overlay's data (docs/VISUALIZER.md §2.17) from the store's fos_* and step events, in the field frame:
    per cell [ix, iy, watched_view_s, watched_spawn_s, deaths, spawns] for the cells watched or holding one
    (deaths: in view; watched: within VIEW_R / SPAWN_R of us), the spawn points, the field sessions counted and
    the geometry."""
    import numpy as np
    x0, y0, x1, y1 = NORMAL_BOX
    dwell = np.zeros((y1 - y0, x1 - x0))
    sessions = con.execute("SELECT session, t FROM events WHERE ev = 'fos_session' AND t >= ? ORDER BY t",
                           (EVENT_START,)).fetchall()
    now = time.time() if now is None else now
    for sid, t0 in sessions:
        steps = con.execute("SELECT t, data FROM events WHERE session = ? AND ev = 'step' AND t BETWEEN ? AND ? "
                            "ORDER BY t", (sid, t0, t0 + FIELD_S)).fetchall()
        prev = None                  # (t, field x, y) of our last position inside the field
        for t, data in steps + [(min(t0 + FIELD_S, now), None)]:
            if prev is not None and t > prev[0]:
                dwell[prev[2] - y0, prev[1] - x0] += min(t - prev[0], WATCH_GAP_S)
            if data is None:
                break
            to = json.loads(data).get("to") or [None, None]
            f = to_field(to[0], to[1])
            prev = (t, f[1], f[2]) if f else None
    view, near = _coverage(dwell, VIEW_R), _coverage(dwell, SPAWN_R)
    counts: dict = {}
    spawns = []
    for (data,) in con.execute("SELECT data FROM events WHERE ev IN ('fos_mob', 'fos_corpse') AND t >= ?",
                               (EVENT_START,)):
        d = json.loads(data)
        f = to_field(d.get("x"), d.get("y"))
        if f is None or d.get("name") != name:
            continue
        if d["ev"] == "fos_corpse" and d.get("in_view"):
            counts.setdefault(_cell_of(f[1], f[2]), [0, 0])[0] += 1
        elif d["ev"] == "fos_mob" and d.get("spawn"):
            counts.setdefault(_cell_of(f[1], f[2]), [0, 0])[1] += 1
            spawns.append([f[1], f[2]])
    keys = {(int(ix), int(iy)) for iy, ix in zip(*np.nonzero(view))} | set(counts)
    cells = [[ix, iy, round(float(view[iy, ix]), 1), round(float(near[iy, ix]), 1), *counts.get((ix, iy), (0, 0))]
             for ix, iy in sorted(keys)]
    return {"name": name, "box": list(NORMAL_BOX), "cell": CELL, "view_r": VIEW_R, "spawn_r": SPAWN_R,
            "instances": [{"name": n, "offset": list(o)} for n, o in INSTANCES],
            "runs": len(sessions), "watched_s": round(float(dwell.sum()), 1), "cells": cells, "spawns": spawns}


# --------------------------------------------------------------------------- backfill
def field_sessions(con) -> list[tuple[int, str]]:
    """(session id, tag) of sessions that entered the field (the server named a spirit) since EVENT_START."""
    return [(sid, tag) for sid, tag in con.execute(
        "SELECT DISTINCT e.session, s.tag FROM events e JOIN sessions s ON s.id = e.session "
        "WHERE e.ev = 'speech_heard' AND e.t >= ? AND e.data LIKE '%If you put the spirit of %' ORDER BY e.session",
        (EVENT_START,))]


def replay_events(base: str) -> list[dict]:
    """The fos_* proxy envelopes a live proxy with FieldLog would have emitted for capture `base`
    (logs/session_<tag>, without the extension)."""
    import contextlib
    import io
    import proxy
    import replay
    import viz_feed
    tap = proxy.SessionTap(viz_feed._Null(), viz_feed._Null(), viz_feed._Null())
    tap.field = FieldLog()
    clock = [0.0]
    tap.wall = lambda: clock[0]
    found = []
    tap.sink = lambda env, facet: found.append(env) if env["data"].get("ev", "").startswith("fos_") else None
    with contextlib.redirect_stdout(io.StringIO()):
        for row in replay.timed_packets(base):
            clock[0] = row[0]
            tap._world(proxy.S2C if row[1] == "s2c" else proxy.C2S, row[3])
    return found


def backfill(db: str, logdir: str, verbose: bool = True) -> dict:
    """Store FieldLog's events for field sessions that have none from a live proxy (replaces earlier backfill
    rows of the session, so a session that grew since is redone)."""
    import memory
    con = memory.connect(db)
    out = {}
    for sid, tag in field_sessions(con):
        live = con.execute("SELECT COUNT(*) FROM events WHERE session = ? AND ev LIKE 'fos_%' AND seq < ?",
                           (sid, BACKFILL_SEQ0)).fetchone()[0]
        base = os.path.join(logdir, f"session_{tag}")
        if live or not os.path.exists(base + ".s2c.raw"):
            out[tag] = "live" if live else "no capture"
            continue
        envs = replay_events(base)
        con.execute("DELETE FROM events WHERE session = ? AND seq >= ?", (sid, BACKFILL_SEQ0))
        con.executemany("INSERT INTO events VALUES(?,?,?,?,?,?)",
                        [(sid, BACKFILL_SEQ0 + i, e["t"], "proxy", e["data"]["ev"], json.dumps(e["data"]))
                         for i, e in enumerate(envs)])
        con.commit()
        kinds = {}
        for e in envs:
            kinds[e["data"]["ev"]] = kinds.get(e["data"]["ev"], 0) + 1
        out[tag] = kinds
        if verbose:
            print(f"{tag}: {kinds}", flush=True)
    con.close()
    return out


def main(argv=None):
    import memory
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=memory.DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill", help="store the fos_* events of field sessions the proxy didn't record")
    b.add_argument("--logdir", default=os.path.join(root, "logs"))
    m = sub.add_parser("map", help="print the reaper map's totals")
    m.add_argument("--name", default=REAPER)
    a = ap.parse_args(argv)
    if a.cmd == "backfill":
        print(json.dumps(backfill(a.db, a.logdir)))
    else:
        con = memory.connect(a.db)
        r = reaper_map(con, a.name)
        cells = r["cells"]
        print(json.dumps({"runs": r["runs"], "watched_min": round(r["watched_s"] / 60, 1), "cells": len(cells),
                          "deaths_in_view": sum(c[4] for c in cells), "spawns": sum(c[5] for c in cells)}))


if __name__ == "__main__":
    main()

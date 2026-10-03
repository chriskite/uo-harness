"""Safety rules for the overseer's own walks (`ctl act goto`).

Until 2026-10-03 a goto was a plain walk: the map route from a Witcher rune to
the Horseshoe Bay moongate crossed a harpy nest, harpies were in view for 17 s,
and nothing reacted until a witch harpy killed Hackworth at the waypoint
(docs/NOTES.md "Cambria Witcher library and a death on an overseer ride"). The
lumber runner had threat checks; the overseer's walk had none. TravelGuard is
the Mover guard a goto runs with (agent_link.Mover calls it after every step):

  - hostile creatures in view (threats.assess: aggressive, i.e. swinging at us,
    war mode, murderer-red or an aggressive body; never a pet, the "(tame)" /
    "(bonded)" / "(summoned)" ones, unless red or swinging at us, and never a
    passive body for war mode alone) become danger zones of radius
    max(flee radius, DANGER_MIN_R) + AVOID_MARGIN around them; the Mover routes
    around zones (agent_link.DANGER_COST_X) and replans when one appears or
    moves. Each creature is recorded once per walk as a `monster_seen` job
    event (job `travel`). Live 2026-10-04 on Shelter Island a tamer's two bonded
    pets and two sheep a player was killing, all in war mode, became zones; the
    sheep made a 44-step walk 142 steps (threats.py docstring, "Pets and
    war-mode passive bodies").
  - a goal inside a danger zone stops the walk (Abort): walking into a nest to
    "arrive" is what killed us.
  - a hostile player in flee range, a red anywhere in view, or "<name> is
    attacking you!" stops the walk: the overseer recalls or decides.
  - under attack with hits below LOW_HITS of max: stop (heal or recall).
    Damage alone doesn't stop the walk: running on, around the attackers, is
    better than standing still.
  - dying stops the walk. A walk started as a ghost (to a healer) is allowed and
    only stops on a hostile player.

What it learns across walks (the store's `monster_seen` job events, written
here and by the lumber runner's escapes): creature bodies seen hostile count as
aggressive from then on (`learned_params`), so a harpy is avoided on sight next
time, before it turns to fight; and the tiles around recent sightings are
costly to route through (`remembered_tiles`, Mover.danger_tiles), so routes
bend around known nests before anything is in view.
"""
import dataclasses
import json
import time

import threats
from agent_link import Abort, cheb

DANGER_MIN_R = 8          # tiles: harpies and their like aggro at about a screen's quarter [INFERENCE]
AVOID_MARGIN = 3
LOW_HITS = 0.5
RECALL_S, MARGIN_S = 2.0, 1.0
MOVED_REPLAN = 3          # a zone that moved this many tiles triggers a replan
REMEMBER_DAYS = 30        # sightings older than this don't shape routes any more
REMEMBER_R = 8            # tiles around a remembered sighting
REMEMBER_WITHIN = 400     # only sightings this close to us (tiles) are loaded


def sightings(memory, days: float = REMEMBER_DAYS) -> list:
    """monster_seen job events (any job) of the last `days`."""
    rows = memory.con.execute("SELECT t, facet, x, y, data FROM job_events WHERE kind = 'monster_seen' "
                              "AND t >= ? ORDER BY t", (time.time() - days * 86400,)).fetchall()
    return [{"t": t, "facet": f, "x": x, "y": y, **json.loads(d)} for t, f, x, y, d in rows]


def learned_params(memory, base: threats.Params = threats.Params()) -> threats.Params:
    """base with every body ever seen hostile added to aggressive_bodies."""
    bodies = {s["body"] for s in sightings(memory, days=100_000) if isinstance(s.get("body"), int)}
    bodies -= set(base.passive_bodies)
    return dataclasses.replace(base, aggressive_bodies=frozenset(set(base.aggressive_bodies) | bodies))


def remembered_tiles(memory, facet, pos, radius: int = REMEMBER_R) -> set:
    """Tiles within `radius` of recent monster sightings near `pos` on `facet`."""
    out = set()
    for s in sightings(memory):
        if s.get("x") is None or (s.get("facet") or 0) != (facet or 0):
            continue
        if pos is not None and cheb((s["x"], s["y"]), pos) > REMEMBER_WITHIN:
            continue
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                out.add((s["x"] + dx, s["y"] + dy))
    return out


def record(memory, st, t: threats.Threat, pos, job: str = "travel"):
    """One monster_seen job event: where a hostile creature was and what it was."""
    memory.job_event(job, "monster_seen",
                     {"serial": f"0x{t.serial:08X}", "name": t.name, "body": t.body, "reason": t.reason,
                      "flee_radius": t.flee_radius},
                     facet=((st.get("world") or {}).get("self") or {}).get("map"), x=pos[0], y=pos[1])


class TravelGuard:
    def __init__(self, mover, memory=None, goal=None, log=print, params: threats.Params | None = None):
        """goal: () -> (x, y) of where the walk is going (checked against live
        zones), or None. With a memory store: learned aggressive bodies,
        remembered monster areas, and sightings recorded."""
        self.mover = mover
        self.memory = memory
        self.goal = goal
        self.log = log
        if params is None:
            params = learned_params(memory) if memory is not None else threats.Params()
        self.watch = threats.Watch(params)
        self.ghost_walk = None            # decided on the first call: dead at the start
        self.seen = set()
        self._attack_scan = len(mover.link.events)
        self.zones_added = 0

    def __call__(self, st):
        a = self.watch.update(st, recall_s=RECALL_S, margin_s=MARGIN_S)
        if self.ghost_walk is None:
            self.ghost_walk = bool(a.dead)
            if self.memory is not None and not self.ghost_walk:
                self.mover.danger_tiles = remembered_tiles(
                    self.memory, ((st.get("world") or {}).get("self") or {}).get("map"), threats.self_pos(st))
        if a.dead and not self.ghost_walk:
            raise Abort("died on the way; stopped walking")
        self._players(a)
        if self.ghost_walk:
            return
        self._creatures(st, a)
        me = (st.get("world") or {}).get("self") or {}
        hits, hmax = me.get("hits"), me.get("hits_max")
        if a.under_attack and hits is not None and hmax and hits < LOW_HITS * hmax:
            raise Abort(f"under attack, hits {hits}/{hmax}; stopped walking (heal or recall)")

    def _players(self, a):
        named = self._attacking_names()
        for t in a.threats:
            if not t.player:
                continue
            if (t.kind == "red" and 0 <= t.distance <= self.watch.params.max_range) \
                    or (t.hostile and t.action == "flee") or t.name in named:
                raise Abort(f"hostile player {t.kind} {t.name or f'0x{t.serial:08X}'} at {t.distance} tiles; "
                            f"stopped walking (recall or decide)")

    def _attacking_names(self) -> set:
        evs = self.mover.link.events
        names = set()
        for e in evs[self._attack_scan:]:
            text = e.get("text") or ""
            if e.get("ev") == "speech_heard" and text.endswith(" is attacking you!"):
                names.add(text[: -len(" is attacking you!")])
        self._attack_scan = len(evs)
        return names

    def _creatures(self, st, a):
        mobs = (st.get("world") or {}).get("mobiles") or {}
        changed = False
        for t in a.threats:
            if t.kind != "monster" or not t.hostile or not 0 <= t.distance <= self.watch.params.max_range:
                continue
            m = mobs.get(f"0x{t.serial:08X}") or {}
            if m.get("x") is None:
                continue
            pos, r = (m["x"], m["y"]), max(t.flee_radius, DANGER_MIN_R) + AVOID_MARGIN
            old = self.mover.danger.get(t.serial)
            if old is None or cheb(old[0], pos) >= MOVED_REPLAN:
                self.mover.danger[t.serial] = (pos, r)
                changed = True
                if old is None:
                    self.zones_added += 1
                    self.log(f"avoiding {t.name or f'0x{t.serial:08X}'} at {pos} (radius {r})")
            if t.serial not in self.seen:
                self.seen.add(t.serial)
                if self.memory is not None:
                    record(self.memory, st, t, pos)
        if not changed:
            return
        self.mover.replan_requested = True
        if self.goal is not None:
            g = tuple(self.goal())
            for key, (c, r) in self.mover.danger.items():
                if cheb(g, c) <= r:
                    m = next((t for t in a.threats if t.serial == key), None)
                    raise Abort(f"the goal {g} is inside the reach of {m.name if m else f'0x{key:08X}'} "
                                f"at {c}; stopped walking")

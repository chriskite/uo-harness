"""Red players (player killers) in the simulated Mausoleum (docs/SIM.md "Red players"; the scenario's `reds`).

Reds are players, so the simulator gives them a player's rules. The numbers and their evidence are in the scenario's
`reds` notes (docs/NOTES.md "Red players met live": 31 live Logan captures; "Red players: the game rules": the wiki,
patch notes, forums):

- **They come in as we do.** A group (1-4 reds) recalls in beside one of the first level's gold moongates (the only
  places magic travel into a dungeon lands) at `arrive_per_h`, and runs the teleporters down to the fight level, the
  only way onto it (user 2026-10-23: (5243, 612) is the door of the Cistern Gorgon's mini-boss room, not a way in;
  the crimson gate is exit-only). On a level it crosses a red attacks only now and then (`attack_p_transit`); on the
  fight level it hunts for `stay_s`, then leaves. Some are already on the fight level when a run starts.
- **They find us by Tracking**: every `tracking.every_s` a check that succeeds at the red's skill, within
  `tracking.range` on the same level, gives our tile at that moment; in view (18) they see us move. They run
  (`step_s` a tile) on the map's walk graph (`Graph`, teleporters included), through mobiles only at full stamina (a
  shove costs `shove_stam`; a Total Refresh tops them up), and follow us through a teleporter they saw us take. They
  don't follow through a moongate (Heat of Battle: no gate for 2 min after attacking a blue).
- **The attack**: a red decides at its first sight of us on a level (`attack_p`); an attacker says "<name> is
  attacking you!" over us once within its kit's `open_r` in sight, then fights by its kit:
  - **mage**: a spell from `opener`, then `mix`, each standing its cast time (`stand`); range (`spell_r`) and sight
    are checked when the cast ends, the cast held (a precast, moving meanwhile) while they fail; the effect `delay`
    later; mana and its regen; casting from within `cast_from_r`, closing to `keep_r`; Teleport to cut corners.
  - **melee**: swings when adjacent (`swing_s`, `hit`, `damage`), Hamstring on a hit (our stamina 0 for 3 s: a walk's
    pace; then immune 30 s), weapon poison, a rope to cut corners; some come in Stealth, hidden until the first swing.
  - **archer**: shots within range in sight after standing `aim_s`; melee (and Hamstring) when adjacent.
  Some bring a summon or pet whose chip damage (`summon`) adds to theirs. A red's hit or spell breaks our recall
  (World.hurt) and flags us in PvP (our rope's 2-minute cooldown, PvP heals: World.flagged).
- **Monsters take them as targets by the rules they take anyone** (World._think); they heal themselves and don't die
  here [INFERENCE: one red of ~50 live appearances died]; they never fight monsters or our pets (live: none did), and
  a hit on a casting red breaks its cast.
- After our death they stand by the corpse (`loot_s`), then roam.

`World.add_red` places one directly (tests, the live-death replays sim/fight*.py): an attacker that knows where we are,
optionally walking a live red's own route first.
"""
from __future__ import annotations

import collections
import math
import random

from sim import packets as P

VIEW = 18
ATTACKED_TEXT = " is attacking you!"


def cheb(a, b) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


# ---------------------------------------------------------------- the dungeon's walk graph
_GRAPHS: dict = {}


class Graph:
    """Every walkable tile of the dungeon reachable from its known arrivals (the client's walk rules, pathfind.Walk),
    each tile's steps, a teleporter's tile stepping straight to its arrival. Built once per process and scenario
    (~0.6 s, ~17k tiles); `field(goal)`: the step count from every tile to `goal` (a reverse BFS, ~10 ms, cached)."""

    def __init__(self, world):
        sc = world.sc
        walk = world.walk
        box = sc["dungeon"]["box"]
        self.tps = {tuple(tp["from"][:2]): tuple(tp["to"]) for tp in sc.get("teleporters", [])}
        seeds = [tp["to"] for tp in sc.get("teleporters", [])]
        seeds += [r["at"] for r in sc["recall"]["rows"]] + [s["at"] for s in sc.get("spawns", [])]
        seeds += [[x, y, 0] for x, y in sc["dungeon"].get("gold_gates", [])]
        if (sc.get("reds") or {}).get("entry"):
            seeds.append(sc["reds"]["entry"])

        def inside(t):
            return box[0] <= t[0] <= box[2] and box[1] <= t[1] <= box[3]

        self.adj: dict[tuple, list] = {}
        q = collections.deque()
        for s in seeds:
            if inside(s):
                n = (s[0], s[1], world.stand_z(s[0], s[1], s[2] if len(s) > 2 else 0))
                if n not in self.adj:
                    self.adj[n] = None
                    q.append(n)
        while q:
            n = q.popleft()
            tp = self.tps.get(n[:2])
            if tp is not None:
                outs = [(tp[0], tp[1], world.stand_z(tp[0], tp[1], tp[2] if len(tp) > 2 else 0))]
            else:
                outs = [tuple(to) for d in range(8)
                        if (to := walk.can_walk(n[0], n[1], n[2], d)) is not None and inside(to)]
            self.adj[n] = outs
            for to in outs:
                if to not in self.adj:
                    self.adj[to] = None
                    q.append(to)
        self.rev = collections.defaultdict(list)
        self.by_xy = collections.defaultdict(list)
        for a, outs in self.adj.items():
            self.by_xy[a[:2]].append(a)
            for b in outs or ():
                self.rev[b].append(a)
        self._fields = collections.OrderedDict()

    @classmethod
    def of(cls, world) -> "Graph":
        key = (world.sc.get("name"), world.facet)
        g = _GRAPHS.get(key)
        if g is None:
            g = _GRAPHS[key] = cls(world)
        return g

    def node(self, pos):
        """The graph's tile at pos: itself, else the one on (x, y) nearest in z, else None."""
        p = (pos[0], pos[1], pos[2] if len(pos) > 2 else 0)
        if p in self.adj:
            return p
        on = self.by_xy.get(p[:2])
        return min(on, key=lambda n: abs(n[2] - p[2])) if on else None

    def near_node(self, pos, r=3):
        """node(pos), else the nearest graph tile within r, else None."""
        n = self.node(pos)
        if n is not None:
            return n
        z = pos[2] if len(pos) > 2 else 0
        best = None
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                for c in self.by_xy.get((pos[0] + dx, pos[1] + dy), ()):
                    k = (max(abs(dx), abs(dy)), abs(c[2] - z))
                    if best is None or k < best[0]:
                        best = (k, c)
        return best[1] if best else None

    def field(self, goal) -> dict:
        f = self._fields.get(goal)
        if f is not None:
            self._fields.move_to_end(goal)
            return f
        f = {goal: 0}
        q = collections.deque([goal])
        rev = self.rev
        while q:
            n = q.popleft()
            dn = f[n] + 1
            for p in rev.get(n, ()):
                if p not in f:
                    f[p] = dn
                    q.append(p)
        self._fields[goal] = f
        if len(self._fields) > 64:
            self._fields.popitem(last=False)
        return f


# ---------------------------------------------------------------- the reds
class Reds:
    """The red players of one World: arrivals (scenario `reds`, when enabled), each red's play (`act`, once a tick from
    World._tick), and what the batches compare with the live numbers (`m`, `summary`)."""

    def __init__(self, world, seed: int, tick_s: float):
        self.w = world
        self.dt = tick_s
        self.cfg = world.sc.get("reds") or {}
        self.on = bool(self.cfg.get("enabled"))
        self.rng = random.Random(seed * 7907 + 29)    # their own draws: the rest of the world's stay put
        self._graph = None
        self._lvl_nodes = {}
        self.arrived_t = None             # our last arrival on the fight level
        self.next_group_t = None
        self.group_n = 0
        self.groups = {}                  # group id -> {"size", "sightings", "last_view_t"}
        self.pending = []                 # (time, group id, spot, stay, at the fight level): a group's later members
        self.m = {"groups_arrived": 0, "reds_arrived": 0, "level_s": 0.0, "walkin_s": 0.0, "appearances": [],
                  "jumps": 0, "hamstrings": 0, "paralyzes": 0, "potions_stuck": 0, "spells": {}, "damage": 0,
                  "monster_on_red_s": 0.0, "disturbed_recalls": 0, "stealth": 0}
        if self.on:
            world.metrics["reds"] = self.m

    # ------------------------------------------------------------ lookups
    @property
    def graph(self) -> Graph:
        if self._graph is None:
            self._graph = Graph.of(self.w)
        return self._graph

    def mobs(self) -> list:
        return [m for m in self.w.mobs.values() if m.kind == "red"]

    def level(self, pos) -> str | None:
        for name, b in (self.cfg.get("levels") or {}).items():
            if b[0] <= pos[0] <= b[2] and b[1] <= pos[1] <= b[3]:
                return name
        return None

    def roll(self, r) -> float:
        if isinstance(r, (int, float)):
            return float(r)
        if len(r) >= 3:
            return self.rng.triangular(r[0], r[1], min(max(r[2], r[0]), r[1]))
        return self.rng.uniform(r[0], r[1])

    def pick(self, weights: dict) -> str:
        keys = list(weights)
        return self.rng.choices(keys, weights=[weights[k] for k in keys])[0]

    # ------------------------------------------------------------ arrivals
    def start(self):
        """The groups on the fight level when the world starts (Poisson: the arrival rate x the mean stay), then the
        first arrival."""
        if not self.on:
            return
        rate = self.cfg["arrive_per_h"] / 3600.0
        st = self.cfg["stay_s"]
        mean = (st[0] + st[1] + (st[2] if len(st) > 2 else (st[0] + st[1]) / 2)) / 3
        n, p, lim = 0, self.rng.random(), math.exp(-rate * mean)
        while p > lim:
            n += 1
            p *= self.rng.random()
        spots = [tuple(s["at"]) for s in self.w.sc["spawns"] if self.level(s["at"]) == self.cfg["hunt_level"]]
        for _ in range(n):
            self._group(self.rng.choice(spots), stay=self.rng.random() * self.roll(st), at_hunt=True)
        self.next_group_t = self.w.t + self.rng.expovariate(rate) if rate > 0 else None

    def tick(self):
        w, t = self.w, self.w.t
        if not self.on:                                  # only placed reds (add_red): when each last saw us
            for m in self.mobs():
                if m.serial in w.viewing:
                    m.rs["last_view_t"] = t
            return
        if self.next_group_t is not None and t >= self.next_group_t:
            gates = w.sc["dungeon"].get("gold_gates") or []
            r = self.cfg.get("arrive_r", 8)
            if gates:
                gx, gy = self.rng.choice(gates)
                near = [n for dx in range(-r, r + 1) for dy in range(-r, r + 1)
                        for n in self.graph.by_xy.get((gx + dx, gy + dy), ())]
                if near:
                    self._group(self.rng.choice(near))
            self.next_group_t = t + self.rng.expovariate(self.cfg["arrive_per_h"] / 3600.0)
        for item in [p for p in self.pending if p[0] <= t]:
            self.pending.remove(item)
            self._member(*item[1:])
        pl = w.player
        if not pl.ghost:
            lvl = self.level(pl.pos)
            if lvl == self.cfg.get("hunt_level"):
                self.m["level_s"] += self.dt
                if self.arrived_t is None:
                    self.arrived_t = t                   # our arrival on the fight level (sightings' since_arrival)
            else:
                self.arrived_t = None
                if lvl in self.cfg.get("walkin_levels", ()):
                    self.m["walkin_s"] += self.dt
        self._sight()

    def _group(self, spot, stay=None, at_hunt=False):
        """A group arriving at `spot`: its size, one pace for all (they run together: live later members came 0-4 s
        behind), a shared knowledge of us and decision per level [INFERENCE: a party], and, with `upper.p`, a while
        hunting the first two levels before going down (live reds on the walk-in levels: NOTES)."""
        g = self.cfg["group"]
        size = self.rng.choices(g["sizes"], weights=g["w"])[0]
        self.group_n += 1
        gid = self.group_n
        up = self.cfg.get("upper") or {}
        upper = 0.0 if at_hunt or self.rng.random() >= up.get("p", 0.0) else self.roll(up.get("stay_s", [60, 600]))
        self.groups[gid] = {"size": size, "sightings": [], "last_view_t": -1e9, "fix": None, "fix_t": -1e9,
                            "decided": {}, "step_s": self.roll(self.cfg.get("step_s", 0.2)),
                            "upper_until": self.w.t + upper}
        self.m["groups_arrived"] += 1
        stay = self.roll(self.cfg["stay_s"]) if stay is None else stay
        self._member(gid, spot, stay, at_hunt)
        for _ in range(size - 1):
            late = self.rng.random() < g.get("late_p", 0.0)
            self.pending.append((self.w.t + self.roll(g["late_s"] if late else g["gap_s"]), gid, spot, stay, at_hunt))

    def _member(self, gid, spot, stay, at_hunt):
        w = self.w
        n = self.graph.near_node(spot)
        if n is None:
            return
        if w._occupied(n[0], n[1]):
            free = [o for o in self.graph.adj.get(n) or () if not w._occupied(o[0], o[1])]
            n = free[0] if free else n
        names = self.cfg.get("names") or ["a murderer"]
        used = {m.name for m in self.mobs()}
        name = self.rng.choice([x for x in names if x not in used] or names)
        kit = self.pick({k: v["w"] for k, v in self.cfg["kits"].items()})
        m = self.add(n[0], n[1], n[2], name=name, kit=kit, attacker=None)
        m.rs["group"], m.rs["stay_s"], m.rs["step_s"] = gid, stay, self.groups[gid]["step_s"]
        if at_hunt:
            m.rs["leave_t"] = w.t + stay
        self.m["reds_arrived"] += 1

    def add(self, x, y, z=None, name="Ticket Tongje", kit="mage", spec=None, route=(), attacker=True):
        """A red at (x, y) (z: the surface nearest ours) with `kit`'s numbers (scenario `reds.kits`, `spec` over
        them); `route`: (x, y, seconds) tiles it walks first, each that long after the last (a live red's own path);
        `attacker` True: it attacks on sight wherever it is (World.add_red); None: it decides at its first sight of us
        on each level (`attack_p`, `attack_p_transit`)."""
        from sim.world import Mob
        w = self.w
        cfg = self.cfg
        base = {k: v for k, v in cfg.items() if k not in ("kits", "note", "names", "levels")}
        spec = {**base, **((cfg.get("kits") or {}).get(kit) or {}), "kit": kit, **(spec or {})}
        z = w.stand_z(x, y, w.player.z if z is None else z)
        hp = spec.get("hits", 120)
        m = Mob(w._serial(), "red", "red player", name, 0x190, x, y, z, hits=hp, hits_max=hp, noto=6, spec=spec)
        r = self.rng
        tr = spec.get("tracking") or {}
        m.rs = {"route": list(route), "route_t": w.t + (route[0][2] if route else 0.0),
                "attacker": attacker, "attacker_level": None, "decided": set(), "fix": None,
                "next_track": w.t + r.uniform(0, tr.get("every_s", 5.0)), "skill": self.roll(tr.get("skill", [0.8, 1.0])),
                "stam": spec.get("stam", 100), "mana": spec.get("mana", 100), "regen_t": w.t + 1.0,
                "cast": None, "held": None, "cast_ready": w.t, "cast_once": False, "flag_t": -1e9,
                "jump_ready": w.t, "jump_check": w.t + 1.0, "jump_to": None, "hamstring_ready": w.t, "tk_ready": w.t,
                "potion_ready": w.t, "aim_since": None, "waypoint": None, "linger_until": 0.0, "leave_t": None,
                "stay_s": None, "follow_tp": None, "summon": r.random() < spec.get("summon_share", 0.0),
                "summon_next": w.t, "step_s": self.roll(spec.get("step_s", 0.2)), "group": None,
                "last_view_t": -1e9, "app": None, "open_r": self.roll(spec.get("open_r", [7, 14])),
                "keep": self.roll(spec.get("keep_r", [1, 4])), "stealth": r.random() < spec.get("stealth_p", 0.0),
                "loot_until": None, "hand": None, "range": None, "hand_serial": w._item_serial()}
        hands = spec.get("hands") or []
        if hands:
            h = r.choices(hands, weights=[x[2] for x in hands])[0]
            m.rs["hand"] = (h[0], h[1]) if h[0] else None
            m.rs["range"] = h[3] if len(h) > 3 else None
        w.mobs[m.serial] = m
        w._view()
        return m

    def show(self, m):
        """What a red holds, when it comes into our view (the runner reads a bow, a crossbow or empty hands as a ranged
        red: loop_hunt.ranged_player)."""
        if m.rs.get("hand"):
            self.w.send(P.equip(m.rs["hand_serial"], m.rs["hand"][0], m.rs["hand"][1], m.serial))

    def _sight(self):
        """Appearances (a red coming into our view after 30 s out of it, we alive) and per group its sightings (its
        first member in view after the whole group was out of it 30 s): docs/NOTES.md's live definitions."""
        w, t, pl = self.w, self.w.t, self.w.player
        if pl.ghost:
            return
        lvl = self.level(pl.pos)
        for m in self.mobs():
            if m.serial not in w.viewing:
                continue
            s = m.rs
            d = cheb(m.pos, pl.pos)
            if t - s["last_view_t"] > 30.0:
                s["app"] = {"t": round(t, 1), "level": lvl, "name": m.name, "kit": m.spec.get("kit"),
                            "group": s["group"], "d0": d, "attack_s": None, "attack_d": None, "min_d": d, "dmg": 0,
                            "fight_s": 0.0}
                self.m["appearances"].append(s["app"])
                g = self.groups.get(s["group"])
                if g is not None:
                    if t - g["last_view_t"] > 30.0:
                        g["sightings"].append({"t": round(t, 1), "level": lvl, "seen": 0, "at": list(pl.pos[:2]),
                                               "red_at": list(m.pos[:2]), "between": self._between(m),
                                               "since_arrival": None if self.arrived_t is None
                                               else round(t - self.arrived_t, 1)})
                    g["sightings"][-1]["seen"] += 1
            if s["app"] is not None:
                s["app"]["min_d"] = min(s["app"]["min_d"], d)
                if s["app"]["attack_s"] is not None:
                    s["app"]["fight_s"] = round(t - s["app"]["t"] - s["app"]["attack_s"], 1)
            s["last_view_t"] = t
            g = self.groups.get(s["group"])
            if g is not None:
                g["last_view_t"] = t

    # ------------------------------------------------------------ one red's play
    def act(self, m):
        w, t, pl = self.w, self.w.t, self.w.player
        s, sp = m.rs, m.spec
        self._resolve(m)
        if t >= s["regen_t"]:
            s["regen_t"] = t + 1.0
            s["mana"] = min(sp.get("mana", 100), s["mana"] + 1.0 / sp.get("mana_regen_s", 1.0))
            s["stam"] = min(sp.get("stam", 100), s["stam"] + 1.0 / sp.get("stam_regen_s", 1.0))
        if s["route"]:
            if t >= s["route_t"]:
                x, y, _ = s["route"].pop(0)
                w._place(m, (x, y, w.stand_z(x, y, m.z)))
                self._took_teleporter(m)
                s["route_t"] = t + (s["route"][0][2] if s["route"] else 0.0)
            self._know(m)
            self._fight(m, moving=False)
            return
        if pl.ghost:
            self._after_kill(m)
            return
        self._know(m)
        if m.target is not None and (self.level(pl.pos) != self.level(m.pos)
                                     or t - s["last_view_t"] > self.cfg.get("lose_s", 30.0)):
            m.target, s["held"] = None, None          # we got away (out of its level, or out of view a while)
            w._warm(m, False)
        hunt = self.cfg.get("hunt_level")
        if s["leave_t"] is None and s["stay_s"] is not None and self.level(m.pos) == hunt:
            s["leave_t"] = t + s["stay_s"]
        if s["leave_t"] is not None and t >= s["leave_t"] and m.target is None and m.serial not in w.viewing:
            self._gone(m)
            return
        self._stealth(m)
        if self._land_jump(m) or self._fight(m, moving=True) or t < m.next_step:
            return
        goal, radius = self._goal(m)
        if goal is None:
            return
        if s["fix"] is not None and goal == s["fix"] and self._jump(m, goal):
            return                                   # closing in on where Tracking put us: a Teleport or a rope
        self._walk_to(m, goal, radius)

    def _attacks_here(self, m) -> bool:
        """It attacks us on this level: decided so here (or anywhere: add_red, a red following us)."""
        s = m.rs
        return bool(s["attacker"]) and s["attacker_level"] in (None, self.level(m.pos))

    def _know(self, m):
        """Where we are, as far as it (and its group: one party) knows: in view (same level, we alive) exactly, and at
        the group's first sight of us on a level its decision to attack; out of view a Tracking check every every_s,
        a success at its skill within range giving our tile then."""
        t, pl = self.w.t, self.w.player
        s, tr = m.rs, m.spec.get("tracking") or {}
        g = self.groups.get(s["group"])
        if pl.ghost:
            return
        lvl = self.level(m.pos)
        if lvl is None or lvl != self.level(pl.pos):
            return
        d = cheb(m.pos, pl.pos)
        got = False
        if d <= VIEW:
            got = True
            if lvl not in s["decided"]:
                s["decided"].add(lvl)
                if s["attacker"] is None or (s["attacker_level"] is not None and s["attacker_level"] != lvl):
                    if g is not None and lvl in g["decided"]:
                        dec = g["decided"][lvl]
                    else:
                        p = self.cfg.get("attack_p", 0.9) if lvl == self.cfg.get("hunt_level") \
                            else self.cfg.get("attack_p_transit", 0.1)
                        dec = self.rng.random() < p
                        if g is not None:
                            g["decided"][lvl] = dec
                    s["attacker"], s["attacker_level"] = dec, lvl
        elif t >= s["next_track"]:
            s["next_track"] = t + tr.get("every_s", 5.0)
            got = d <= tr.get("range", 50) and self.rng.random() < s["skill"]
        if got:
            s["fix"] = pl.pos
            if g is not None:
                g["fix"], g["fix_t"] = pl.pos, t
        elif g is not None and g["fix"] is not None and t - g["fix_t"] < 5.0:
            s["fix"] = g["fix"]

    def _goal(self, m):
        """(goal tile, radius) of its next steps: through the teleporter it saw us take; to where Tracking put us (an
        attacker, or one yet to decide where it hunts); on down to the fight level (after its group's while on the
        first levels); else roaming its level, standing about a while here and there."""
        t, pl = self.w.t, self.w.player
        s = m.rs
        hunt = self.cfg.get("hunt_level")
        lvl = self.level(m.pos)
        if s["follow_tp"] is not None:
            if t - s["follow_tp"][0] <= 30.0:
                return s["follow_tp"][1], 0
            s["follow_tp"] = None
        g = self.groups.get(s["group"])
        here = lvl == hunt or (g is not None and t < g["upper_until"] and lvl in self.cfg.get("walkin_levels", ()))
        hunting = self._attacks_here(m) or (s["attacker"] is None and here)
        if hunting and s["fix"] is not None and self.level(s["fix"]) == lvl:
            if cheb(m.pos, s["fix"]) <= 1 and cheb(m.pos, pl.pos) > VIEW:
                s["fix"] = None                    # where Tracking put us: gone, look again
                if g is not None:
                    g["fix"] = None
            else:
                return s["fix"], 1
        if not here and self.cfg.get("entry"):
            return tuple(self.cfg["entry"]), 0
        if s["linger_until"] > t:
            return None, 0
        if s["waypoint"] is None or cheb(m.pos, s["waypoint"]) <= 1 or self.level(s["waypoint"]) != lvl:
            if s["waypoint"] is not None and self.level(s["waypoint"]) == lvl:
                s["linger_until"] = t + self.roll(self.cfg.get("linger_s", [5, 40]))
            near = [n for n in self._level_nodes(lvl) if 3 < cheb(n, m.pos) <= self.cfg.get("roam_r", 40)]
            s["waypoint"] = self.rng.choice(near) if near else None
            return None, 0
        return s["waypoint"], 1

    def _between(self, m) -> bool | None:
        """Whether the red stands nearer the leave gate by the walk than we do (on our way out: live, at a group's first
        sight, 7 of 12 such groups killed Logan, 0 of 5 others; docs/NOTES.md "Red players met live")."""
        gate = (self.w.sc.get("runner") or {}).get("leave_gate")
        if not gate or self.level(m.pos) != self.cfg.get("hunt_level"):
            return None
        g = self.graph
        gn = g.near_node((gate[0], gate[1], 0))
        f = g.field(gn) if gn is not None else {}
        a, b = f.get(g.node(m.pos)), f.get(g.node(self.w.player.pos))
        return None if a is None or b is None else a < b

    def _level_nodes(self, lvl) -> list:
        """The walk graph's tiles on a level (roaming's waypoints)."""
        if lvl not in self._lvl_nodes:
            self._lvl_nodes[lvl] = [n for n in self.graph.adj if self.level(n) == lvl]
        return self._lvl_nodes[lvl]

    def _walk_to(self, m, goal, radius) -> bool:
        """One step toward `goal` along the graph's field (a teleporter on the way taken), through a mobile only at full
        stamina (a shove); blocked below it, a Total Refresh first."""
        w, t = self.w, self.w.t
        s, sp = m.rs, m.spec
        if cheb(m.pos, goal) <= radius and self.level(m.pos) == self.level(goal):
            return False
        pace = sp.get("stealth_step_s", 0.67) if m.hidden_until > t else s["step_s"]
        g = self.graph
        node, gn = g.node(m.pos), g.near_node(goal)
        f = g.field(gn) if gn is not None else None
        cur = f.get(node) if f is not None and node is not None else None
        if cur is None:
            m.next_step = w._due(m.next_step, pace)
            return w._step_toward(m, goal, radius)
        cands = sorted(g.adj.get(node) or (), key=lambda n: (f.get(n, 1 << 30), cheb(n, goal)))
        full = s["stam"] >= sp.get("stam", 100)
        for n in cands:
            dn = f.get(n)
            if dn is None or dn >= cur:
                break
            if w._occupied(n[0], n[1], m.serial):
                if not full:
                    continue
                s["stam"] -= sp.get("shove_stam", 10)
            m.next_step = w._due(m.next_step, pace)
            if cheb(n, m.pos) > 1:                     # standing on a teleporter's tile: its arrival
                m.x, m.y, m.z, m.path = n[0], n[1], n[2], []
                w._moved(m)
            else:
                w._place(m, n)
                self._took_teleporter(m)
            return True
        if not full and cands and f.get(cands[0], 1 << 30) < cur:
            s["stam"] = sp.get("stam", 100)               # a Total Refresh (no cooldown: wiki Alchemy #13498)
            m.next_step = t + sp.get("refresh_s", 0.5)
            return False
        m.next_step = t + s["step_s"]
        return False

    def _took_teleporter(self, m):
        """A red on a teleporter's tile goes through it (as we do: World._walk)."""
        to = self.graph.tps.get((m.x, m.y))
        if to is None:
            return
        w = self.w
        m.x, m.y, m.z, m.path = to[0], to[1], w.stand_z(to[0], to[1], to[2] if len(to) > 2 else 0), []
        m.rs["follow_tp"] = None
        w._moved(m)

    def player_teleported(self, frm):
        """We stepped onto a teleporter at `frm`: every red attacking us with us in view follows us through it and keeps
        attacking beyond (live 2026-10-22 18:53:14 Botan, 3 s behind, then his attack on the second level)."""
        w = self.w
        lvl = self.level(w.player.pos)
        for m in self.mobs():
            if self._attacks_here(m) and self.level(m.pos) == lvl and cheb(m.pos, w.player.pos) <= VIEW:
                m.rs["follow_tp"] = (w.t, (frm[0], frm[1], w.player.z))
                m.rs["attacker_level"] = None

    def _gone(self, m):
        w = self.w
        if m.serial in w.viewing:
            w.send(P.delete(m.serial))
            w.viewing.discard(m.serial)
        for o in w.mobs.values():
            if o.target == m.serial:
                o.target = None
            o.threat.pop(m.serial, None)
        del w.mobs[m.serial]

    def _after_kill(self, m):
        """We died: it stands by the corpse a while (looting), then roams its level."""
        w, t = self.w, self.w.t
        s = m.rs
        if m.target is not None:
            m.target, s["cast"], s["held"], m.casts = None, None, None, []
            w._warm(m, False)
            s["loot_until"] = t + self.roll(self.cfg.get("loot_s", [20, 300]))
            s["attacker"], s["fix"] = False, None
        if t < m.next_step:
            return
        if s["loot_until"] is not None and t < s["loot_until"]:
            if cheb(m.pos, w.player.pos) > 1:
                self._walk_to(m, w.player.pos, 1)
            return
        goal, radius = self._goal(m)
        if goal is not None:
            self._walk_to(m, goal, radius)

    def _stealth(self, m):
        """A Stealth red (kit `stealth_p`) hides out of our view or sight as it closes in (hiding needs it unseen:
        wiki Hiding #14157), and walks at Stealth's pace until its first swing reveals it."""
        w, pl = self.w, self.w.player
        s = m.rs
        if not s["stealth"] or m.hidden_until or m.target is not None or not self._attacks_here(m) \
                and not (s["attacker"] is None and self.level(m.pos) == self.cfg.get("hunt_level")):
            return
        if self.level(m.pos) != self.level(pl.pos):
            return
        d = cheb(m.pos, pl.pos)
        if d <= m.spec.get("stealth_r", 24) and (d > VIEW or not w.can_see(m, pl)):
            m.hidden_until = 1e18
            self.m["stealth"] += 1
            if m.serial in w.viewing:
                w.send(P.delete(m.serial))
                w.viewing.discard(m.serial)

    def _reveal(self, m):
        if m.hidden_until:
            m.hidden_until = 0.0
            self.w._view()

    # ------------------------------------------------------------ the fight
    def _fight(self, m, moving: bool) -> bool:
        """Its fight with us: an attacker on our level with us in view: the attack line, then its kit. True when it
        decided its own movement this tick (a cast, an aim, a swing, a step at us)."""
        w, t, pl = self.w, self.w.t, self.w.player
        s, sp = m.rs, m.spec
        if s["held"] is not None and t - s["held"]["words_t"] > sp.get("hold_s", 30.0):
            s["held"] = None
        if pl.ghost or not self._attacks_here(m) or self.level(m.pos) != self.level(pl.pos):
            return False
        d = cheb(m.pos, pl.pos)
        if d > VIEW:
            return False                           # out of view: Tracking leads it (_goal)
        sees = w.can_see(m, pl)
        if m.target is None:
            if m.hidden_until:
                if d <= 1:
                    self._reveal(m)
                    self._attack_line(m)
                    m.next_swing = t
            elif d <= s["open_r"] and sees:
                self._attack_line(m)
        kit = sp.get("kit", "mage")
        if kit == "mage":
            self._mage(m, d, sees, moving)
        elif kit == "archer" and d > 1:
            self._archer(m, d, sees, moving)
        else:
            self._melee(m, d, sees, moving)
        return True

    def _attack_line(self, m):
        w, t, pl = self.w, self.w.t, self.w.player
        m.target = pl.serial
        w._warm(m, True)
        w.send(P.says(pl.serial, pl.name, m.name + ATTACKED_TEXT, kind=0, hue=34, body=pl.body))
        w.attacked_by_player_t = w.pvp_t = t
        m.rs["flag_t"] = t
        m.next_swing = max(m.next_swing, t)
        w.count("red_attacks")
        app = m.rs.get("app")
        if app is not None and app["attack_s"] is None:
            app["attack_s"] = round(t - app["t"], 1)
            app["attack_d"] = cheb(m.pos, pl.pos)

    def _step_at(self, m, radius, sees):
        """A step toward us (to within radius and our sight) when its step is due; a jump first when it pays."""
        t, pl = self.w.t, self.w.player
        if t < m.next_step or (cheb(m.pos, pl.pos) <= radius and sees):
            return
        if not self._jump(m):
            self._walk_to(m, pl.pos, radius if sees else 1)

    def _jump(self, m, goal=None) -> bool:
        """Teleport (a mage: "Rel Por", standing 1 s) or a rope (others: hindered 1 s) to the tile within 10 in its
        sight nearest `goal` (us) by the walk, when that brings it `jump.min_gain` steps nearer (the 1 s stand costs
        ~5 running steps), `jump.min_d` steps or more off; tried once a second at `jump.p`.
        Cooldowns: the rope 5 s; flagged in PvP the rope 2 min, Teleport 15 s (wiki Adventurer's Rope #13326)."""
        w, t, pl = self.w, self.w.t, self.w.player
        s, j = m.rs, m.spec.get("jump") or {}
        if not j or t < s["jump_ready"] or t < s["jump_check"] or m.hidden_until:
            return False
        s["jump_check"] = t + 1.0
        if self.rng.random() >= j.get("p", 0.0):
            return False
        g = self.graph
        node, gn = g.node(m.pos), g.near_node(goal or pl.pos)
        if node is None or gn is None:
            return False
        f = g.field(gn)
        cur = f.get(node)
        if cur is None or cur < j.get("min_d", 6):
            return False
        r = j.get("r", 10)
        best = None
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                for n in g.by_xy.get((m.x + dx, m.y + dy), ()):
                    dn = f.get(n)
                    if dn is None or cur - dn < j.get("min_gain", 6) \
                            or (best is not None and dn >= best[0]) or w._occupied(n[0], n[1]) \
                            or not w.sight.can_see(m.pos, n):
                        continue
                    best = (dn, n)
        if best is None:
            return False
        flagged = t - s["flag_t"] <= self.cfg.get("pvp_flag_s", 60.0)
        if m.spec.get("kit") == "mage":
            if s["cast"] is not None or s["held"] is not None or s["mana"] < 9:
                return False
            s["mana"] -= 9
            w._overhead(m, "Rel Por", kind=10)
            s["jump_ready"] = t + (j.get("teleport_flagged_cd_s", 15.0) if flagged else 1.0)
        else:
            s["jump_ready"] = t + (j.get("rope_flagged_cd_s", 120.0) if flagged else j.get("rope_cd_s", 5.0))
        stand = j.get("stand_s", 1.0)
        s["jump_to"] = (t + stand, best[1])
        m.next_step = t + stand + 0.05
        if m.serial in w.viewing:
            self.m["jumps"] += 1                         # in our view, as the live ones were counted
        return True

    def _land_jump(self, m) -> bool:
        """A jump under way: lands when due (True while it is still on its way)."""
        s = m.rs
        if s["jump_to"] is None:
            return False
        if self.w.t < s["jump_to"][0]:
            return True
        _, to = s["jump_to"]
        s["jump_to"] = None
        if not self.w._occupied(to[0], to[1]):
            m.x, m.y, m.z, m.path = to[0], to[1], to[2], []
            self.w._moved(m)
            self._took_teleporter(m)
        return False

    def _mage(self, m, d, sees, moving):
        t = self.w.t
        s, sp = m.rs, m.spec
        if self._land_jump(m):
            return
        if s["cast"] is not None:
            if t < s["cast"]["target_t"]:
                return                                  # standing to cast
            s["held"], s["cast"] = s["cast"], None      # the target cursor up: taken below, held while it can't be
        if s["held"] is not None:
            c = s["held"]
            spl = sp["spells"][c["key"]]
            if spl.get("self"):                         # a heal on itself (Greater Heal): no target to wait for
                s["held"] = None
                m.hits = m.hits_max
                s["cast_ready"] = t + self.roll(sp.get("recover_s", [0.2, 0.6]))
            elif d <= sp.get("spell_r", 12) and sees:
                s["held"] = None
                m.casts.append([t + self.roll(spl.get("delay", 0.0)), c["key"]])   # wherever we go by then
                s["cast_ready"] = t + self.roll(sp.get("recover_s", [0.2, 0.6]))
        elif m.target is not None and t >= s["cast_ready"] and d <= sp.get("cast_from_r", 8):
            key = self._choose_spell(m)
            if key is not None:
                spl = sp["spells"][key]
                s["mana"] -= spl.get("mana", 0)
                self.w._overhead(m, spl["words"], kind=10)
                stand = self.roll(spl["stand"])
                s["cast"] = {"key": key, "words_t": t, "target_t": t + stand}
                m.next_step = max(m.next_step, t + stand)
                self.m["spells"][key] = self.m["spells"].get(key, 0) + 1
                return
        if moving:
            self._step_at(m, s["keep"], sees)

    def _choose_spell(self, m) -> str | None:
        s, sp, t = m.rs, m.spec, self.w.t
        table = sp["mix"] if s["cast_once"] else sp["opener"]
        s["cast_once"] = True
        for _ in range(8):
            key = self.pick(table)
            spl = sp["spells"].get(key) or {}
            if spl.get("mana", 0) > s["mana"] or (key == "tk" and (t < s["tk_ready"] or t < s["potion_ready"])) \
                    or (key == "para" and self.w.paralyzed_until > t) \
                    or (spl.get("self") and m.hits >= m.hits_max * spl.get("below", 0.8)):
                continue
            return key
        cheap = [k for k in sp["mix"] if sp["spells"][k].get("damage") and sp["spells"][k].get("mana", 0) <= s["mana"]]
        return min(cheap, key=lambda k: sp["spells"][k]["mana"]) if cheap else None

    def _resolve(self, m):
        """What reaches us now: a spell's effect, a stuck potion's blast, a summon's chip."""
        w, t, pl = self.w, self.w.t, self.w.player
        sp = m.spec
        for c in [c for c in m.casts if t >= c[0]]:
            m.casts.remove(c)
            if pl.ghost or self.level(pl.pos) != self.level(m.pos):
                continue
            key = c[1]
            tk = (sp.get("spells") or {}).get("tk", {}).get("potion") or {}
            if key == "stick":
                w._sys("An explosion potion has stuck to you!")
                self.m["potions_stuck"] += 1
                m.casts.append([t + self.roll(tk.get("blast_s", [2, 5])), "potion"])
                continue
            if key == "potion":
                self._hit(m, self.roll(tk.get("damage", [33, 41])), "spell")
                continue
            spl = sp["spells"][key]
            if spl.get("damage"):
                self._hit(m, self.roll(spl["damage"]), "spell")
            if spl.get("poison") and not pl.ghost:
                w._specials(m, pl, {"specials": [{"kind": "poison", "chance": 1.0, **spl["poison"]}]})
            if spl.get("paralyze_s") and not pl.ghost and self.rng.random() >= spl.get("resist", 0.0):
                w.paralyze_player(spl["paralyze_s"])
                self.m["paralyzes"] += 1
            if spl.get("potion"):
                po = spl["potion"]
                m.rs["tk_ready"] = t + po.get("tk_cd_s", 30.0)
                m.rs["potion_ready"] = t + po.get("cd_s", 15.0)
                m.casts.append([t + self.roll(po.get("throw_s", [0.5, 1.5])), "stick"])
        su = sp.get("summon") or {}
        if m.rs["summon"] and su and m.target is not None and not pl.ghost and t >= m.rs["summon_next"]:
            m.rs["summon_next"] = t + su.get("every_s", 3.0)
            if self.level(pl.pos) == self.level(m.pos) and cheb(m.pos, pl.pos) <= su.get("range", 8) \
                    and w.can_see(m, pl):
                self._hit(m, self.roll(su["damage"]), "spell")

    def _hit(self, m, n, kind):
        w = self.w
        before = w.player.hits
        if n >= 0.5:
            w.hurt(w.player, n, m, kind=kind)
            done = max(0, int(before - w.player.hits))
            self.m["damage"] += done
            if m.rs.get("app") is not None:
                m.rs["app"]["dmg"] += done

    def _melee(self, m, d, sees, moving):
        w, t, pl = self.w, self.w.t, self.w.player
        s, sp = m.rs, m.spec
        if self._land_jump(m):
            return
        if d <= 1 and abs(m.z - pl.z) <= 15:
            if m.target is None:
                self._reveal(m)
                self._attack_line(m)
            if t >= m.next_swing:
                mel = sp.get("melee") or {}
                m.next_swing = w._due(m.next_swing, self.roll(mel.get("swing_s", [1.34, 1.51])))
                if self.rng.random() < mel.get("hit", 0.59):
                    self._hit(m, self.roll(mel.get("damage", [10, 28, 13])), "melee")
                    hs = mel.get("hamstring")
                    if hs and t >= s["hamstring_ready"] \
                            and w.hamstring_player(hs.get("s", 3.0), hs.get("immune_s", 30.0)):
                        s["hamstring_ready"] = t + self.roll(hs.get("cd_s", [30, 53]))
                        self.m["hamstrings"] += 1
                    if mel.get("poison") and not pl.ghost:
                        w._specials(m, pl, {"specials": [{"kind": "poison", **mel["poison"]}]})
            return
        if moving:
            self._step_at(m, 1, sees)

    def _archer(self, m, d, sees, moving):
        t = self.w.t
        s, sp = m.rs, m.spec
        if self._land_jump(m):
            return
        a = sp.get("archery") or {}
        rng = s["range"] or a.get("range", 8)
        if m.target is not None and d <= rng and sees:
            if s["aim_since"] is None:
                s["aim_since"] = t                    # standing to aim (wiki Archery: 0.5-1.0 s in PvP)
            if t - s["aim_since"] >= s.setdefault("aim_s", self.roll(a.get("aim_s", [0.5, 1.0]))) and t >= m.next_swing:
                m.next_swing = t + self.roll(a.get("swing_s", [1.45, 1.55]))
                m.next_step = max(m.next_step, t + a.get("move_delay_s", 0.5))
                if self.rng.random() < a.get("hit", 0.6):
                    self._hit(m, self.roll(a.get("damage", [10, 26, 13])), "melee")
            return
        s["aim_since"] = None
        if moving:
            self._step_at(m, max(1, rng - 2), sees)

    # ------------------------------------------------------------ summaries
    def summary(self) -> dict:
        """The run's red numbers: what run.py aggregates against the live ones (docs/SIM.md "Red players")."""
        return {**self.m, "sightings": [{**sg, "group": gid, "size": g["size"]} for gid, g in self.groups.items()
                                        for sg in g["sightings"]]}

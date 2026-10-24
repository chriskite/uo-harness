"""Observed dungeon facts from proxy captures, for the offline simulator (harness/sim).

    python harness/sim/calibrate.py [--scenario NAME] [--logs DIR] [--out PATH] [--scan YYYYMMDD] [TAG ...]

Replays each capture (logs/session_<TAG>.*, default the scenario's `calibration.tags`) through
world.runtime.WorldRuntime with replay.timed_packets and writes what happened while our
player stood in the scenario's region (`calibration.facet` / `.box`: the Mausoleum's facet 0,
x 5150-5650, y 600-760; Tidal Tomb's x 6600-6975, y 1900-2115) to
harness/data/sim/<NAME>_observed.json: levels and teleporter jumps, per creature type
(bodies, notoriety, first-seen spots, movement cadence, damage dealt to the pets / us,
swing period, time to kill, damage the pets dealt, gold, aggro), per pet, the player,
the bard skills and respawns. `--scan 20261010` also replays every session in the logs
dir from that date on and adds the ones whose player was ever inside the region.
Red players (notoriety 6 with a player's body or flag) are no creature type: `source` counts them.

Every number comes with `n` (sample size). Attribution rules are spelled out in the
output's `rules` object; anything not directly counted is marked [INFERENCE] there.
Deterministic: same captures in, byte-identical JSON out (times are seconds relative to
the first in-region packet of each session, plus local clock strings).
"""

import argparse
import bisect
import collections
import datetime
import glob
import json
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.dirname(HERE)
REPO = os.path.dirname(HARNESS)
if HARNESS not in sys.path:
    sys.path.insert(0, HARNESS)

import replay  # noqa: E402
import tamer  # noqa: E402
import threats  # noqa: E402
from world.runtime import WorldRuntime  # noqa: E402

DEFAULT_SCENARIO = "mausoleum"
DEFAULT_LOGS = os.path.join(REPO, "logs")
SCENARIO_DIR = os.path.join(HARNESS, "data", "sim")

# The region of the scenario configure() last read (the Mausoleum's until then: sim/cast_scan.py's in_region)
FACET = 0
X_RANGE = (5150, 5650)
Y_RANGE = (600, 760)


def configure(name: str) -> list[str]:
    """Read scenario `name`'s `calibration` block (facet, box, tags): the region in_region tests from now on;
    its default capture tags."""
    global FACET, X_RANGE, Y_RANGE
    with open(os.path.join(SCENARIO_DIR, f"{name}.json"), encoding="utf-8") as f:
        cal = json.load(f)["calibration"]
    x0, y0, x1, y1 = cal["box"]
    FACET, X_RANGE, Y_RANGE = cal.get("facet", 0), (x0, x1), (y0, y1)
    return list(cal["tags"])


def red_player(m) -> bool:
    """A red player (sim/red_scan.py's rule): notoriety 6 and flag 0x20 or a human body, not a ghost."""
    return (m.notoriety == 6 and m.graphic not in threats.GHOST_BODIES
            and (bool((m.flags or 0) & 0x20) or m.graphic in threats.HUMAN_BODIES))


MONSTER_NOTORIETY = (3, 4, 5, 6)   # attackable, criminal, enemy, murderer (1 = other players and their pets)
WAR = 0x40                         # 0x20 MobileUpdate flags: war mode
TELEPORT_TILES = 5                 # a self position jump larger than this in one update is a teleporter
MELEE_R = 1
RANGED_R = 12
SAME_SWING_S = 0.25                # numbers on one victim from one attacker this close are one swing
SWING_GAP_S = 6.0                  # a longer gap between hits is a new bout, not a swing period
STEP_GAP_S = 2.0                   # a longer gap between two moves of a creature is a pause
WALK_GAP_S = 0.6                   # our own steps: a longer gap ends a walking run
FIGHT_GAP_S = 15.0                 # damage numbers this far apart belong to separate engagements
VET_WINDOW_S = 0.6
DEVOUR_WINDOW_S = 1.6              # the gravebug's '*devours corpse*' heals 3 ticks at +0.0/+0.5/+1.0 s
POTION_WINDOW_S = 0.6
AGGRO_FOLLOW_S = 4.0
SAMPLE_S = 0.5                     # pet distance samples
SPAWN_IN_VIEW = 12                 # first sight this close to us: it appeared, it did not walk in
RESPAWN_R = 3
BARD_TIMEOUT_S = 10.0
OUR_HUE = 89                       # '-N' over a creature in this hue: damage from our side [INFERENCE]
OTHER_HUE = 2417                   # ...in this hue: provoked creatures / poison ticks [INFERENCE]
GOLD = 0x0EED

NUMBER = re.compile(r"^([+-])(\d+)$")
BARD_PROMPT = re.compile(r"^(What do you wish to (discord|pacify|incite)\?|Whom do you wish them to attack\?)")
TEXT_POTION = "You drink a healing potion."
TEXT_DEVOUR = "*devours corpse*"
TEXT_BREAK_AVOIDED = "Barding break avoided"
CLILOC_BANDAGE_PROMPT = 500948
CLILOC_LOS = {500237: "refused_cannot_see", 500446: "refused_too_far"}
SKILLS = tamer.SKILL_NAMES


def in_region(x, y, facet):
    return (facet == FACET and x is not None and y is not None
            and X_RANGE[0] <= x <= X_RANGE[1] and Y_RANGE[0] <= y <= Y_RANGE[1])


def cheb(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


def clock(t):
    return datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S")


def type_key(name):
    n = " ".join((name or "").lower().split())
    for art in ("a ", "an ", "the "):
        if n.startswith(art):
            return n[len(art):]
    return n


def stats(values, digits=2):
    """{n, median, p10, p90, min, max, mean} of numbers; {n: 0} for none."""
    v = sorted(values)
    if not v:
        return {"n": 0}

    def q(p):
        return v[min(len(v) - 1, int(p * (len(v) - 1) + 0.5))]
    return {"n": len(v), "median": round(statistics.median(v), digits), "p10": round(q(0.1), digits),
            "p90": round(q(0.9), digits), "min": round(v[0], digits), "max": round(v[-1], digits),
            "mean": round(statistics.fmean(v), digits)}


def histogram(values):
    c = collections.Counter(values)
    return {"n": len(values), "counts": {str(k): c[k] for k in sorted(c)}}


# ---------------------------------------------------------------------------
# One pass over a capture
# ---------------------------------------------------------------------------

class Session:
    """Everything recorded while our player stood in the region, from one capture."""

    def __init__(self, tag, logs):
        self.tag = tag
        self.base = os.path.join(logs, "session_" + tag)
        self.t0 = self.t1 = None
        self.self_serial = None
        self.self_name = None
        self.track = []            # (t, x, y, z, facet) on every change
        self.steps = []            # (t, run) of our confirmed moves
        self.pets = {}             # serial -> name (notoriety 2, tame/bonded, seen in the region)
        self.info = {}             # serial -> dict(name, graphics, notorieties, first, deaths)
        self.obs = collections.defaultdict(list)   # serial -> [(t, pid, x, y, z, war, d_self, {pet: d})]
        self.mob_hits = collections.defaultdict(list)  # monster serial -> [(t, amount, hue, ctx)]
        self.victim_hits = []      # '-N' on pets / us: dict
        self.heals = []            # '+N' on pets / us: (t, serial, amount)
        self.system = []           # (t, text)
        self.clilocs = []          # (t, number)
        self.overhead = []         # (t, serial, text) non-number speech over mobiles
        self.deaths = []           # dict
        self.lifts = []            # dict
        self.anims = collections.defaultdict(list)  # serial -> [(t, action)]
        self.pet_samples = []      # (t, pet, distance, war, last pet command)
        self.commands = []         # (t, phrase) pet commands we said
        self.bard = []             # one dict per skill use
        self.players_seen = set()  # other players in the region (reds too)
        self.reds = set()          # red players in the region (red_player): no creature type
        self.packets_in_region = 0
        self.armor = collections.Counter()   # our status AR (0x11 physical_resist) -> seconds in the region
        self._ar = None

    # -- helpers on the live state -----------------------------------------
    def _monster(self, s, m):
        return (s != self.self_serial and s not in self.pets and s not in self.reds
                and m.notoriety in MONSTER_NOTORIETY)

    def _war_monsters_near(self, rt, x, y, r):
        out = []
        for s, m in rt.state.mobiles.items():
            if m.x is None or not self._monster(s, m) or not (m.flags or 0) & WAR:
                continue
            if cheb(m.x, m.y, x, y) <= r:
                out.append(s)
        return sorted(out)

    def _pet_distances(self, rt, x, y):
        d = {}
        for p in self.pets:
            m = rt.state.mobiles.get(p)
            if m is not None and m.x is not None:
                d[p] = cheb(m.x, m.y, x, y)
        return d

    def _note_mobile(self, s, m, t):
        i = self.info.get(s)
        if i is None:
            i = self.info[s] = {"name": None, "graphics": set(), "notorieties": set(), "first": None}
        if m.name:
            i["name"] = m.name
        if m.graphic is not None:
            i["graphics"].add(m.graphic)
        if m.notoriety is not None:
            i["notorieties"].add(m.notoriety)
        return i

    # -- the pass ------------------------------------------------------------
    def run(self):
        packets = replay.timed_packets(self.base)
        now = [None]
        rt = WorldRuntime(clock=lambda: now[0])
        st = rt.state
        last_pos = None
        next_sample = None
        bard_open = None
        bard_prompt_live = False
        for t, d, _src, p in packets:
            now[0] = t
            rt.feed_packet(d, p)
            me = st.self
            if self.self_serial is None and me.serial is not None:
                self.self_serial = me.serial
            pos = (me.x, me.y, me.z, me.map)
            if pos != last_pos:
                self.track.append((t,) + pos)
                last_pos = pos
            inside = in_region(me.x, me.y, me.map)
            events = rt.drain_events()
            if not inside:
                continue
            self.packets_in_region += 1
            if self.t0 is None:
                self.t0 = t
            ar = st.self.stats.get("physical_resist")
            if self.t1 is not None and self._ar is not None:
                self.armor[self._ar] += t - self.t1
            self._ar = ar
            self.t1 = t
            if self.self_name is None:
                self.self_name = st.self.name
            # pets: ours are notoriety 2 and tame/bonded
            for s, m in st.mobiles.items():
                if s not in self.pets and m.notoriety == 2 and m.pet in ("tame", "bonded") and m.name:
                    self.pets[s] = m.name
            pid = p[0]
            if d == "s2c" and pid in (0x20, 0x77):
                s = int.from_bytes(p[1:5], "big")
                m = st.mobiles.get(s)
                if m is not None and m.x is not None and s != self.self_serial:
                    i = self._note_mobile(s, m, t)
                    if m.notoriety == 1 and m.graphic in (400, 401, 605, 606):
                        self.players_seen.add(s)
                    elif red_player(m):
                        self.reds.add(s)
                        self.players_seen.add(s)
                    if i["first"] is None:
                        i["first"] = {"t": t, "pos": [m.x, m.y, m.z], "d_self": cheb(m.x, m.y, me.x, me.y)}
                    self.obs[s].append((t, pid, m.x, m.y, m.z, bool((m.flags or 0) & WAR),
                                        cheb(m.x, m.y, me.x, me.y), self._pet_distances(rt, m.x, m.y)))
            if next_sample is None:
                next_sample = t
            while next_sample <= t:
                for ps in sorted(self.pets):
                    m = st.mobiles.get(ps)
                    if m is not None and m.x is not None:
                        self.pet_samples.append((next_sample, ps, cheb(m.x, m.y, me.x, me.y),
                                                 bool((m.flags or 0) & WAR),
                                                 self.commands[-1][1] if self.commands else None))
                next_sample += SAMPLE_S
            for e in events:
                ev = e["ev"]
                if ev == "speech_heard":
                    text, s = e["text"], e["serial"]
                    if s == 0xFFFFFFFF:
                        self.system.append((t, text))
                        if BARD_PROMPT.match(text):
                            bard_prompt_live = True
                        elif text.endswith("?"):
                            bard_prompt_live = False
                        if bard_open is not None:
                            self._bard_answer(bard_open, t, text=text)
                        continue
                    m = st.mobiles.get(s)
                    if m is not None and s != self.self_serial:
                        self._note_mobile(s, m, t)
                    num = NUMBER.match(text) if e["type"] == 0 else None
                    if num is None:
                        if e["type"] != 6:
                            self.overhead.append((t, s, text))
                        continue
                    amount = int(num.group(2))
                    if num.group(1) == "+":
                        if s == self.self_serial or s in self.pets:
                            self.heals.append((t, s, amount))
                        continue
                    if s == self.self_serial or s in self.pets:
                        vx, vy = (me.x, me.y) if s == self.self_serial else (m.x, m.y) if m else (None, None)
                        if vx is None:
                            continue
                        self.victim_hits.append({
                            "t": t, "victim": s, "amount": amount, "hue": e["hue"],
                            "ar": st.self.stats.get("physical_resist") if s == self.self_serial else None,
                            "melee": self._war_monsters_near(rt, vx, vy, MELEE_R),
                            "ranged": self._war_monsters_near(rt, vx, vy, RANGED_R)})
                    elif m is not None and self._monster(s, m) and m.x is not None:
                        others = sum(1 for o in self.players_seen
                                     if o in st.mobiles and st.mobiles[o].x is not None
                                     and cheb(st.mobiles[o].x, st.mobiles[o].y, m.x, m.y) <= 18)
                        self.mob_hits[s].append((t, amount, e["hue"], {
                            "pets": self._pet_distances(rt, m.x, m.y), "d_self": cheb(m.x, m.y, me.x, me.y),
                            "other_players_18": others}))
                elif ev == "cliloc":
                    self.clilocs.append((t, e["cliloc"]))
                    if e["cliloc"] == CLILOC_BANDAGE_PROMPT:
                        bard_prompt_live = False
                    if bard_open is not None:
                        self._bard_answer(bard_open, t, cliloc=e["cliloc"])
                elif ev == "command" and e["type"] == 36:
                    skill = int((e["text"] or "0").split()[0])
                    if skill in SKILLS:
                        bard_open = {"t": t, "skill": skill, "targets": [], "outcome": None, "self": [me.x, me.y]}
                        bard_prompt_live = False
                        self.bard.append(bard_open)
                elif ev == "target_response":
                    if (bard_open is not None and bard_prompt_live and bard_open["outcome"] is None
                            and t - bard_open["t"] <= BARD_TIMEOUT_S):
                        s = e["serial"]
                        m = st.mobiles.get(s)
                        tgt = {"serial": s, "dt": round(t - bard_open["t"], 3)}
                        if m is not None and m.x is not None:
                            tgt.update(name=m.name, d_self=cheb(m.x, m.y, me.x, me.y))
                        elif s == self.self_serial:
                            tgt.update(name="self", d_self=0)
                        elif s == 0:
                            tgt.update(name="ground" if (e["x"] or e["y"]) else "cancel")
                        bard_open["targets"].append(tgt)
                        bard_prompt_live = False
                elif ev == "mobile_death":
                    s = e["serial"]
                    last = self.obs[s][-1] if self.obs.get(s) else None
                    self.deaths.append({"t": t, "serial": s, "corpse": e["corpse"], "corpse_name": e["name"],
                                        "pos": [last[2], last[3], last[4]] if last else None})
                elif ev == "lift":
                    it = st.items.get(e["serial"])
                    chain, c = [], (it.container if it else None)
                    while c is not None and len(chain) < 4:
                        chain.append(c)
                        parent = st.items.get(c)
                        c = parent.container if parent is not None else None
                    self.lifts.append({"t": t, "serial": e["serial"], "amount": e["amount"],
                                       "graphic": it.graphic if it else None, "containers": chain})
                elif ev == "animation":
                    self.anims[e["serial"]].append((t, e["action"]))
                elif ev == "speech":
                    phrase = " ".join((e["text"] or "").split())
                    if phrase.lower() in tamer.COMMANDS:
                        self.commands.append((t, tamer.COMMANDS[phrase.lower()]))
                elif ev == "walk_confirm" and e["moved"]:
                    self.steps.append(t)
        # final names (labels arrive late; corpses name the dead)
        for s, m in st.mobiles.items():
            if s in self.info and m.name:
                self.info[s]["name"] = m.name
        for s, e in st.last_seen.items():
            if s in self.info and e.get("name"):
                self.info[s]["name"] = e["name"]
        for dth in self.deaths:
            i = self.info.get(dth["serial"])
            if i is not None and not i["name"]:
                n = dth["corpse_name"] or ""
                if n.endswith(" corpse"):
                    i["name"] = n[:-len(" corpse")]
        return self

    @staticmethod
    def _bard_answer(use, t, text=None, cliloc=None):
        if use["outcome"] is not None or t - use["t"] > BARD_TIMEOUT_S:
            return
        out = None
        if text is not None:
            if tamer.ANSWER_OK.match(text):
                out = "ok"
                use["duration_s"] = tamer.duration(text)
            elif tamer.ANSWER_FAIL.match(text):
                out = "beyond" if tamer.TEXT_BEYOND in text else "fail"
                use["estimate"] = text.split("estimate the task to be", 1)[-1].strip(" .") if "estimate" in text else None
            elif text == tamer.TEXT_CALM_WAIT:
                out = "refused_calm_wait"
            elif text == tamer.TEXT_IMMUNE:
                out = "refused_barding_break"
            elif text.startswith(TEXT_BREAK_AVOIDED):
                use["break_avoided"] = True
        elif cliloc is not None:
            if cliloc == tamer.CLILOC_SKILL_WAIT and not use["targets"]:
                out = "refused_skill_wait"
            elif cliloc in CLILOC_LOS:
                out = CLILOC_LOS[cliloc]
        if out is not None:
            use["outcome"] = out
            use["answer_dt"] = round(t - use["t"], 3)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def creature_type(sess, s):
    i = sess.info.get(s) or {}
    name = i.get("name")
    pet_names = {n.lower() for n in sess.pets.values()}
    if name and name.lower() in pet_names:
        return "transfixed copy of " + name.lower()
    if name:
        return type_key(name)
    g = sorted(i.get("graphics") or [])
    return "unnamed body " + ",".join(str(x) for x in g)


def levels(sessions):
    """Clusters of our positions between teleporter jumps, merged when their boxes touch."""
    segs, jumps = [], []
    for sess in sessions:
        cur = None
        prev = None
        for t, x, y, z, f in sess.track:
            if not in_region(x, y, f):
                prev = None
                cur = None
                continue
            if prev is not None and cheb(prev[1], prev[2], x, y) > TELEPORT_TILES:
                jumps.append({"tag": sess.tag, "clock": clock(t), "from": [prev[1], prev[2], prev[3]], "to": [x, y, z]})
                cur = None
            if cur is None:
                cur = {"x": [x, x], "y": [y, y], "z": [z, z], "t": [t, t], "tiles": set()}
                segs.append(cur)
            for k, v in (("x", x), ("y", y), ("z", z)):
                cur[k][0] = min(cur[k][0], v)
                cur[k][1] = max(cur[k][1], v)
            cur["t"][1] = t
            cur["tiles"].add((x, y))
            prev = (t, x, y, z)
    # merge segments whose boxes overlap within 2 tiles and whose z ranges overlap within 5
    groups = []
    for sg in segs:
        hit = None
        for g in groups:
            if (sg["x"][0] <= g["x"][1] + 2 and g["x"][0] <= sg["x"][1] + 2 and sg["y"][0] <= g["y"][1] + 2
                    and g["y"][0] <= sg["y"][1] + 2 and sg["z"][0] <= g["z"][1] + 5 and g["z"][0] <= sg["z"][1] + 5):
                hit = g
                break
        if hit is None:
            groups.append({"x": list(sg["x"]), "y": list(sg["y"]), "z": list(sg["z"]),
                           "seconds": sg["t"][1] - sg["t"][0], "tiles": set(sg["tiles"])})
        else:
            for k in ("x", "y", "z"):
                hit[k] = [min(hit[k][0], sg[k][0]), max(hit[k][1], sg[k][1])]
            hit["seconds"] += sg["t"][1] - sg["t"][0]
            hit["tiles"] |= sg["tiles"]
    out = []
    for n, g in enumerate(groups):
        out.append({"level": n, "x": g["x"], "y": g["y"], "z": g["z"], "seconds": round(g["seconds"], 1),
                    "distinct_tiles_stood": len(g["tiles"])})
    for j in jumps:
        for n, g in enumerate(out):
            for end in ("from", "to"):
                x, y, _ = j[end]
                if g["x"][0] <= x <= g["x"][1] and g["y"][0] <= y <= g["y"][1]:
                    j.setdefault(end + "_level", n)
    return {"clusters": out, "teleports": jumps,
            "note": "Bounding boxes of the tiles our player stood on, split at self position jumps > "
                    f"{TELEPORT_TILES} tiles inside the region (teleporters), merged when boxes touch (2 tiles, "
                    "z within 5). Only where the player walked: the rooms are larger [INFERENCE]."}


def swings(hits):
    """Group (t, amount) of one attacker into swings (numbers within SAME_SWING_S) -> [(t, total)]."""
    out = []
    for t, a in sorted(hits):
        if out and t - out[-1][0] <= SAME_SWING_S:
            out[-1] = (out[-1][0], out[-1][1] + a)
        else:
            out.append((t, a))
    return out


def creatures(sessions):
    per = collections.defaultdict(lambda: {
        "graphics": set(), "notorieties": set(), "serials": [], "first_seen": [],
        "steps": {"war": [], "peace": []}, "tiles": {"war": [0, 0.0], "peace": [0, 0.0]},
        "dmg_pets": [], "dmg_pets_clean": [], "dmg_us": [], "dmg_us_ar": collections.defaultdict(list), "dmg_ranged": [],
        "swing_melee": [], "swing_amounts": [],
        "kill_s": [], "hurt_rate": [], "hurt_total_killed": [], "hurt_other_total": [],
        "gold": [], "aggro": [], "deaths": 0})
    for sess in sessions:
        pets = set(sess.pets)
        # monsters: any serial ever seen at a monster notoriety
        monsters = [s for s, i in sess.info.items()
                    if s not in pets and s not in sess.reds and i["first"] is not None
                    and i["notorieties"] & set(MONSTER_NOTORIETY)]
        death_by_serial = {d["serial"]: d for d in sess.deaths}
        for s in sorted(monsters):
            k = creature_type(sess, s)
            c = per[k]
            i = sess.info[s]
            c["graphics"] |= i["graphics"]
            c["notorieties"] |= i["notorieties"]
            c["serials"].append((sess.tag, s))
            c["first_seen"].append(i["first"]["pos"] + [i["first"]["d_self"]])
            obs = sess.obs.get(s, [])
            # movement from 0x77 updates that changed the tile
            prev = None
            for o in obs:
                if o[1] != 0x77:
                    continue
                if prev is not None and (o[2], o[3]) != (prev[2], prev[3]):
                    dt = o[0] - prev[0]
                    if 0 < dt <= STEP_GAP_S:
                        key = "war" if prev[5] else "peace"
                        c["steps"][key].append(dt)
                        c["tiles"][key][0] += cheb(o[2], o[3], prev[2], prev[3])
                        c["tiles"][key][1] += dt
                if prev is None or (o[2], o[3]) != (prev[2], prev[3]):
                    prev = o
            # damage it took
            hits = sess.mob_hits.get(s, [])
            ours = [(t, a) for t, a, h, _ in hits if h == OUR_HUE]
            other = [(t, a) for t, a, h, _ in hits if h != OUR_HUE]
            dth = death_by_serial.get(s)
            if dth is not None:
                c["deaths"] += 1
                if hits:
                    c["kill_s"].append(dth["t"] - hits[0][0])
                    c["hurt_total_killed"].append(sum(a for _, a, _, _ in hits))
                    c["hurt_other_total"].append(sum(a for _, a in other))
            if len(ours) >= 2:
                end = dth["t"] if dth is not None else ours[-1][0]
                span = end - ours[0][0]
                if span > 0:
                    c["hurt_rate"].append(sum(a for _, a in ours) / span)
            # gold from its corpse
            if dth is not None:
                g = [lf["amount"] for lf in sess.lifts if lf["graphic"] == GOLD and dth["corpse"] in lf["containers"]]
                if g:
                    c["gold"].append(sum(g))
            # aggro: the first switch to war mode
            ag = aggro(sess, s, obs, hits)
            if ag is not None:
                c["aggro"].append(ag)
        # damage on pets / us
        by_attacker = collections.defaultdict(list)
        ill = collections.defaultdict(list)
        for t, s, x in sess.overhead:
            if x == tamer.TEXT_POISONED:
                ill[s].append(t)
        for h in sess.victim_hits:
            if len(h["melee"]) == 1:
                a = h["melee"][0]
                k = creature_type(sess, a)
                (per[k]["dmg_us"] if h["victim"] == sess.self_serial else per[k]["dmg_pets"]).append(h["amount"])
                if h["victim"] == sess.self_serial and h["ar"]:
                    per[k]["dmg_us_ar"][h["ar"]].append(h["amount"])
                if h["victim"] != sess.self_serial and not nearest_within(ill[h["victim"]], h["t"], tamer.POISON_S, after=False):
                    per[k]["dmg_pets_clean"].append(h["amount"])
                by_attacker[a].append((h["t"], h["amount"]))
            elif not h["melee"] and len(h["ranged"]) == 1:
                per[creature_type(sess, h["ranged"][0])]["dmg_ranged"].append(h["amount"])
        for a, hs in by_attacker.items():
            k = creature_type(sess, a)
            sw = swings(hs)
            per[k]["swing_amounts"].extend(x for _, x in sw)
            per[k]["swing_melee"].extend(b[0] - a_[0] for a_, b in zip(sw, sw[1:]) if b[0] - a_[0] <= SWING_GAP_S)
    out = {}
    for k in sorted(per):
        c = per[k]
        tiles = {w: round(c["tiles"][w][0] / c["tiles"][w][1], 2) if c["tiles"][w][1] else None for w in ("war", "peace")}
        out[k] = {
            "graphics": sorted(c["graphics"]),
            "notorieties": sorted(c["notorieties"]),
            "n_serials": len(c["serials"]),
            "deaths_seen": c["deaths"],
            "first_seen": {"n": len(c["first_seen"]), "positions": [p[:3] for p in c["first_seen"]],
                           "distance_from_us": stats([p[3] for p in c["first_seen"]], 1)},
            "movement": {
                "step_interval_s_war": stats(c["steps"]["war"], 3),
                "step_interval_s_peace": stats(c["steps"]["peace"], 3),
                "tiles_per_s_war": {"n": len(c["steps"]["war"]), "value": tiles["war"]},
                "tiles_per_s_peace": {"n": len(c["steps"]["peace"]), "value": tiles["peace"]},
            },
            "damage_to_pets": stats(c["dmg_pets"], 1),
            "damage_to_pets_victim_not_poisoned": stats(c["dmg_pets_clean"], 1),
            "damage_to_us": stats(c["dmg_us"], 1),
            "damage_to_us_by_ar": {str(a): stats(v, 1) for a, v in sorted(c["dmg_us_ar"].items())},
            "damage_ranged_sole": stats(c["dmg_ranged"], 1),
            "swing": {"period_s": stats(c["swing_melee"], 2), "damage_per_swing": stats(c["swing_amounts"], 1)},
            "kill_s": stats(c["kill_s"], 1),
            "damage_taken_total_killed": stats(c["hurt_total_killed"], 0),
            "damage_taken_other_hue_killed": stats(c["hurt_other_total"], 0),
            "hurt_rate": stats(c["hurt_rate"], 1),
            "gold": stats(c["gold"], 0),
            "aggro": aggro_summary(c["aggro"]),
        }
    return out


def aggro(sess, s, obs, hits):
    """Distance to us / nearest pet when the creature first went to war mode, and whom it closed on."""
    if not obs:
        return None
    sw = None
    for n, o in enumerate(obs):
        if o[5]:
            sw = n
            break
    if sw is None:
        return None
    o = obs[sw]
    pet_d = min(o[7].values()) if o[7] else None
    first_hit = hits[0][0] if hits else None
    rec = {"tag": sess.tag, "serial": f"0x{s:08X}", "clock": clock(o[0]), "d_self": o[6], "d_pet": pet_d,
           "first_seen_in_war": sw == 0, "hit_before": first_hit is not None and first_hit <= o[0]}
    later = [x for x in obs[sw + 1:] if x[0] - o[0] <= AGGRO_FOLLOW_S]
    if later:
        e = later[-1]
        e_pet = min(e[7].values()) if e[7] else None
        ds = o[6] - e[6]
        dp = (pet_d - e_pet) if pet_d is not None and e_pet is not None else None
        if ds <= 0 and (dp is None or dp <= 0):
            toward = "neither"
        elif dp is None or ds > dp:
            toward = "player"
        elif dp > ds:
            toward = "pet"
        else:
            toward = "both"
        rec.update(toward=toward, closed_self=ds, closed_pet=dp)
    else:
        rec["toward"] = "no_update"
    return rec


def aggro_summary(recs):
    switched = [r for r in recs if not r["first_seen_in_war"]]
    return {
        "n": len(recs),
        "n_switched_in_view": len(switched),
        "d_self_at_switch": stats([r["d_self"] for r in switched], 1),
        "d_pet_at_switch": stats([r["d_pet"] for r in switched if r["d_pet"] is not None], 1),
        "hit_before_switch": sum(1 for r in switched if r["hit_before"]),
        "toward": dict(sorted(collections.Counter(r["toward"] for r in recs).items())),
        "records": recs,
    }


def nearest_within(times, t, window, before=True, after=True):
    """True if some time in sorted `times` lies within [t-window, t+window] (sides as given)."""
    lo = t - window if before else t
    hi = t + window if after else t
    i = bisect.bisect_left(times, lo)
    return i < len(times) and times[i] <= hi


def pets_section(sessions):
    out = {}
    split = collections.defaultdict(list)
    for sess in sessions:
        vet_finish = sorted(t for t, x in sess.system if x == tamer.TEXT_VET_FINISH)
        for p, name in sorted(sess.pets.items()):
            o = out.setdefault(name, {"serials": [], "dmg": [], "war_s": 0.0, "heals": collections.defaultdict(list),
                                      "dist_war": [], "dist_peace": [], "dist_by_cmd": collections.defaultdict(list),
                                      "attack_anim_war": [], "dealt": [], "body": None, "steps_peace": []})
            o["serials"].append(p)
            o["body"] = sorted(sess.info.get(p, {}).get("graphics", []))
            # time in war mode from consecutive observations
            obs = sess.obs.get(p, [])
            for a, b in zip(obs, obs[1:]):
                if a[5] and b[0] - a[0] <= 10:
                    o["war_s"] += b[0] - a[0]
            # one-tile moves out of war mode: the pace it follows us at (scenario pets' step_s)
            o["steps_peace"].extend(b[0] - a[0] for a, b in zip(obs, obs[1:])
                                    if not a[5] and not b[5] and cheb(a[2], a[3], b[2], b[3]) == 1
                                    and 0 < b[0] - a[0] <= STEP_GAP_S)
            o["dmg"].extend(h["amount"] for h in sess.victim_hits if h["victim"] == p)
            devours = sorted(t for t, s, x in sess.overhead if s == p and x == TEXT_DEVOUR)
            for t, s, a in sess.heals:
                if s != p:
                    continue
                if nearest_within(vet_finish, t, VET_WINDOW_S):
                    o["heals"]["vet"].append(a)
                elif nearest_within(devours, t, DEVOUR_WINDOW_S, after=False):
                    o["heals"]["devour"].append(a)
                else:
                    o["heals"]["other"].append(a)
            for t, ps, dist, war, cmd in sess.pet_samples:
                if ps != p:
                    continue
                (o["dist_war"] if war else o["dist_peace"]).append(dist)
                if not war:
                    o["dist_by_cmd"][cmd or "none"].append(dist)
            # attack animations (the pet's most common action) -> attack period, gaps <= SWING_GAP_S
            acts = sess.anims.get(p, [])
            if acts:
                main = collections.Counter(a for _, a in acts).most_common(1)[0][0]
                ts = [t for t, a in acts if a == main]
                o["attack_action"] = main
                o["attack_anims"] = o.get("attack_anims", 0) + len(ts)
                o["attack_anim_war"].extend(b - a for a, b in zip(ts, ts[1:]) if b - a <= SWING_GAP_S)
        # damage dealt: classify every our-hue number over a monster
        gb = next((p for p in sorted(sess.pets) if 387 in sess.info.get(p, {}).get("graphics", ())), None)
        ph = next((p for p in sorted(sess.pets) if 832 in sess.info.get(p, {}).get("graphics", ())), None)
        gb_action = out[sess.pets[gb]].get("attack_action") if gb is not None else None
        gb_anims = sorted(t for t, a in sess.anims.get(gb, []) if a == gb_action)
        for s, hits in sorted(sess.mob_hits.items()):
            for t, a, hue, ctx in hits:
                if hue != OUR_HUE:
                    split["other_hue"].append(a)
                    continue
                if ctx["other_players_18"]:
                    split["skipped_other_player_near"].append(a)
                    continue
                dg = ctx["pets"].get(gb)
                if dg is not None and dg > MELEE_R and ph in ctx["pets"]:
                    split["phoenix_gravebug_not_adjacent"].append(a)
                    out[sess.pets[ph]]["dealt"].append(a)
                elif dg is not None and dg <= MELEE_R and nearest_within(gb_anims, t, 0.15, after=False):
                    split["gravebug_adjacent_after_its_attack_anim"].append(a)
                    out[sess.pets[gb]]["dealt"].append(a)
                else:
                    split["unattributed_gravebug_adjacent_no_anim"].append(a)
    res = {}
    for name, o in out.items():
        war_min = o["war_s"] / 60.0
        res[name] = {
            "serials": [f"0x{s:08X}" for s in o["serials"]],
            "body": o["body"],
            "seconds_in_war_mode": round(o["war_s"], 1),
            "damage_taken": stats(o["dmg"], 1),
            "damage_taken_per_war_minute": {"n": len(o["dmg"]),
                                            "value": round(sum(o["dmg"]) / war_min, 1) if war_min else None},
            "heals": {k: dict(stats(v, 1), total=sum(v)) for k, v in sorted(o["heals"].items())},
            "distance_from_us_fighting": histogram(o["dist_war"]),
            "distance_from_us_not_fighting": histogram(o["dist_peace"]),
            "distance_not_fighting_by_last_command": {k: histogram(v) for k, v in sorted(o["dist_by_cmd"].items())},
            "step_interval_s_not_fighting": stats(o["steps_peace"], 3),
            "attack_animation": {"action": o.get("attack_action"), "n_total": o.get("attack_anims", 0),
                                 "period_s": stats(o["attack_anim_war"], 2)},
            "damage_dealt_attributed": stats(o["dealt"], 1),
        }
    total = sum(sum(v) for k, v in split.items() if k != "other_hue")
    party = {k: dict(stats(v, 1), total=sum(v)) for k, v in sorted(split.items())}
    party["our_hue_total"] = total
    return res, party


def engagements(sess):
    """Damage numbers (on creatures, pets, us) clustered with FIGHT_GAP_S gaps."""
    ts = sorted([h["t"] for h in sess.victim_hits] + [t for hs in sess.mob_hits.values() for t, _, _, _ in hs])
    fights = []
    for t in ts:
        if fights and t - fights[-1][1] <= FIGHT_GAP_S:
            fights[-1][1] = t
        else:
            fights.append([t, t])
    return fights


def player_section(sessions):
    per_fight, dmg_hits, potions, vet_self, other_heals, walk_rates, fight_len, kills = [], [], [], [], [], [], [], []
    for sess in sessions:
        me = sess.self_serial
        potion_t = sorted(t for t, x in sess.system if x == TEXT_POTION)
        vet_finish = sorted(t for t, x in sess.system if x == tamer.TEXT_VET_FINISH)
        for a, b in engagements(sess):
            d = sum(h["amount"] for h in sess.victim_hits if h["victim"] == me and a <= h["t"] <= b)
            per_fight.append(d)
            fight_len.append(b - a)
            kills.append(sum(1 for x in sess.deaths if a <= x["t"] <= b + 2 and x["serial"] in sess.mob_hits))
        dmg_hits.extend(h["amount"] for h in sess.victim_hits if h["victim"] == me)
        for t, s, amt in sess.heals:
            if s != me:
                continue
            if nearest_within(potion_t, t, POTION_WINDOW_S):
                potions.append(amt)
            elif nearest_within(vet_finish, t, VET_WINDOW_S):
                vet_self.append(amt)
            else:
                other_heals.append(amt)
        run = []
        for t in sess.steps:
            if run and t - run[-1] > WALK_GAP_S:
                if len(run) >= 4:
                    walk_rates.append((len(run) - 1) / (run[-1] - run[0]))
                run = []
            run.append(t)
        if len(run) >= 4:
            walk_rates.append((len(run) - 1) / (run[-1] - run[0]))
    return {
        "engagements": {"n": len(per_fight), "duration_s": stats(fight_len, 1), "kills": stats(kills, 1),
                        "damage_to_us_per_engagement": stats(per_fight, 0)},
        "damage_to_us_hits": stats(dmg_hits, 1),
        "potions": {"drunk": sum(1 for s in sessions for _, x in s.system if x == TEXT_POTION),
                    "satchel_used": sum(1 for s in sessions for _, x in s.system if x == "Potion satchel used."),
                    "heal_amount": stats(potions, 1)},
        "vet_supplies_heal_on_us": stats(vet_self, 1),
        "other_heals_on_us": stats(other_heals, 1),
        "walk_steps_per_s": stats(walk_rates, 2),
        "damage_to_us_per_minute_in_region": {
            "n": len(dmg_hits), "minutes": round(sum(s.t1 - s.t0 for s in sessions) / 60.0, 2),
            "value": round(sum(dmg_hits) / (sum(s.t1 - s.t0 for s in sessions) / 60.0), 1)},
    }


def bard_section(sessions):
    by_skill = {}
    for sess in sessions:
        for u in sess.bard:
            sk = SKILLS[u["skill"]]
            b = by_skill.setdefault(sk, {"uses": 0, "outcomes": collections.Counter(), "by_type": {},
                                         "distance_by_outcome": collections.defaultdict(list),
                                         "answer_dt": [], "durations": collections.Counter(),
                                         "estimates": collections.Counter(), "break_avoided": 0})
            b["uses"] += 1
            outcome = u["outcome"] or ("no_answer" if u["targets"] else "no_target")
            b["outcomes"][outcome] += 1
            if u.get("break_avoided"):
                b["break_avoided"] += 1
            if u.get("duration_s") is not None:
                b["durations"][u["duration_s"]] += 1
            if u.get("estimate"):
                b["estimates"][u["estimate"]] += 1
            if u.get("answer_dt") is not None and u["targets"]:
                b["answer_dt"].append(u["answer_dt"] - u["targets"][-1]["dt"])
            tgt = u["targets"][0] if u["targets"] else None
            if tgt is not None:
                tk = creature_type(sess, tgt["serial"]) if tgt["serial"] in sess.info else tgt.get("name") or "unknown"
                b["by_type"].setdefault(tk, collections.Counter())[outcome] += 1
                if tgt.get("d_self") is not None:
                    b["distance_by_outcome"][outcome].append(tgt["d_self"])
    out = {}
    for sk in sorted(by_skill):
        b = by_skill[sk]
        out[sk] = {"n": b["uses"], "outcomes": dict(sorted(b["outcomes"].items())),
                   "by_type": {k: dict(sorted(v.items())) for k, v in sorted(b["by_type"].items())},
                   "target_distance_by_outcome": {k: stats(v, 1) for k, v in sorted(b["distance_by_outcome"].items())},
                   "answer_after_target_s": stats(b["answer_dt"], 3),
                   "success_durations_s": {str(k): v for k, v in sorted(b["durations"].items())},
                   "fail_estimates": dict(sorted(b["estimates"].items())),
                   "barding_break_avoided": b["break_avoided"]}
    return out


def respawn_section(sessions):
    recs = []
    appeared = 0
    for sess in sessions:
        deaths = [(d["t"], creature_type(sess, d["serial"]), d["pos"]) for d in sess.deaths if d["pos"]]
        for s, i in sess.info.items():
            if s in sess.pets or s in sess.reds or i["first"] is None or not i["notorieties"] & set(MONSTER_NOTORIETY):
                continue
            f = i["first"]
            if f["d_self"] > SPAWN_IN_VIEW:
                continue
            appeared += 1
            k = creature_type(sess, s)
            for t, dk, pos in deaths:
                if dk == k and t < f["t"] and cheb(pos[0], pos[1], f["pos"][0], f["pos"][1]) <= RESPAWN_R:
                    recs.append({"tag": sess.tag, "type": k, "serial": s, "delay_s": round(f["t"] - t, 1),
                                 "death_pos": pos, "appeared_pos": f["pos"], "d_self": f["d_self"]})
                    break
    return {"first_seen_within_view": appeared, "candidates": recs, "delay_s": stats([r["delay_s"] for r in recs], 1)}


def damage_hues(sessions):
    c = collections.Counter()
    for sess in sessions:
        for hits in sess.mob_hits.values():
            for _, _, h, _ in hits:
                c[("creature", h)] += 1
        for h in sess.victim_hits:
            c[("us" if h["victim"] == sess.self_serial else "pet", h["hue"])] += 1
    return {f"{k[0]}:{k[1]}": v for k, v in sorted(c.items())}


RULES = {    # with "region" first (build: the region configure() read)
    "monster": f"a mobile seen at notoriety {list(MONSTER_NOTORIETY)} that is not one of our pets (ours: notoriety 2 and tame/bonded) "
               "nor a red player (notoriety 6 with flag 0x20 or a human body: source.captures[].red_players_seen).",
    "type_key": "lowercase name without a leading article; mobiles named like our pets but not ours are 'transfixed copy of <pet>' "
                "(they appear right after a vampire countess's '*attempts to transfix*' and '*transfixed*' over them: [INFERENCE] a countess ability).",
    "war_mode": "flag 0x40 of the latest 0x20 MobileUpdate (Outlands 0x77 carries no flags).",
    "movement": f"intervals between consecutive 0x77 updates that changed the tile, gaps > {STEP_GAP_S}s dropped (pauses); war/peace by the flag at the earlier update; tiles/s = tiles moved / time over those pairs.",
    "damage_to_pets/us": f"a '-N' (speech type 0) over a pet / us counts for a creature type when exactly one war-mode monster stood within {MELEE_R} tile of the victim; '_victim_not_poisoned' drops numbers within {tamer.POISON_S}s after '*looks violently ill*' over the victim (poison ticks); 'damage_ranged_sole' = no war-mode monster within {MELEE_R} and exactly one within {RANGED_R}.",
    "player_armor": "source.captures[].player_armor_s: seconds in the region at each of our status ARs (0x11 physical_resist; 0 = a ghost); a creature's damage_to_us_by_ar: its damage_to_us numbers by our AR when they landed (sim/armor_scan.py tests the armor rule on them, docs/SIM.md 'Player armor').",
    "swing": f"attributed numbers from one creature within {SAME_SWING_S}s are one swing (summed); period = gap between its successive swings when <= {SWING_GAP_S}s. Numbers include poison/bleed ticks the rule can't separate.",
    "kill_s": "first '-N' over the creature (any hue) to its mobile_death.",
    "hurt_rate": f"sum of '-N' in hue {OUR_HUE} over the creature / (death or last such number - first such number), creatures with >= 2 numbers.",
    "damage_hues": f"'-N' over creatures came in hue {OUR_HUE} (our pets' hits) and {OTHER_HUE} (only during a successful Provocation between two provoked creatures, and poison ticks after '*looks violently ill*'): [INFERENCE] the hue marks damage from our side; pets/us take damage in 946.",
    "damage_taken_total_killed": "sum of every '-N' over a creature that died in view: a lower bound on its hit points (numbers sent while it was out of view or before we arrived are missed) [INFERENCE].",
    "gold": f"lifts of graphic 0x{GOLD:04X} out of the corpse (mobile_death.corpse, up to 4 container levels) of that creature, summed per corpse.",
    "aggro": f"the first 0x20 that showed the creature in war mode: distances to us and the nearest pet then; 'toward' compares how much those distances shrank by the last update within {AGGRO_FOLLOW_S}s. 'first_seen_in_war' = already in war mode when it came into view (distances then are not aggro ranges). 'hit_before' = a '-N' over it came earlier (the pets opened).",
    "pet_heals": f"'+N' over a pet within {VET_WINDOW_S}s of 'You finish using veterinary supplies.' = vet; within {DEVOUR_WINDOW_S}s after its '*devours corpse*' = devour; else other.",
    "pet_damage_dealt": f"[INFERENCE] the gravebug melees, so a hue-{OUR_HUE} '-N' over a monster while the gravebug stood > {MELEE_R} tile away is the phoenix's (we never fought); for the gravebug only numbers while adjacent within 0.15 s after its attack animation. Numbers with another player within 18 tiles are skipped.",
    "pet_distance": f"Chebyshev distance pet-us sampled every {SAMPLE_S}s; fighting = pet in war mode; not fighting split by the last pet command we said.",
    "potion": f"'+N' over us within {POTION_WINDOW_S}s of 'You drink a healing potion.'",
    "engagement": f"damage numbers (creatures, pets, us) clustered with gaps <= {FIGHT_GAP_S}s; kills = mobile_death of a creature that took a number, within the engagement + 2 s.",
    "walk": f"confirmed own steps grouped into runs (gap <= {WALK_GAP_S}s, >= 4 steps); rate = steps / run time.",
    "bard": "a skill use (0x12 type 0x24 for 9/15/22) owns the next bard prompt's target response (the first target; provocation's second is ignored) and the first answer within 10 s: ok (ANSWER_OK), fail / beyond (ANSWER_FAIL, 'beyond your skill'), refused_calm_wait, refused_barding_break, refused_skill_wait (cliloc 500118 before any target), refused_cannot_see / refused_too_far (cliloc 500237 / 500446); no_target = no answer and no target; distances are us to the target at the answer.",
    "respawn": f"a monster first seen <= {SPAWN_IN_VIEW} tiles from us (it appeared rather than walked into view) within {RESPAWN_R} tiles of where one of its type died earlier.",
}


def scan(logs, since):
    """Tags from `since` (YYYYMMDD) on whose player ever stood in the region: [{tag, packets_in_region}]."""
    out = []
    for path in sorted(glob.glob(os.path.join(logs, "session_*.s2c.raw"))):
        tag = os.path.basename(path)[len("session_"):-len(".s2c.raw")]
        if tag[:8] < since or os.path.getsize(path) == 0:
            continue
        try:
            packets = replay.timed_packets(os.path.join(logs, "session_" + tag))
        except (OSError, ValueError) as ex:
            out.append({"tag": tag, "error": str(ex)})
            continue
        rt = WorldRuntime(clock=lambda: 0.0)
        n = 0
        for _t, d, _src, p in packets:
            rt.feed_packet(d, p)
            rt.drain_events()
            me = rt.state.self
            if in_region(me.x, me.y, me.map):
                n += 1
        out.append({"tag": tag, "packets_in_region": n})
    return out


def build(tags, logs, scanned=None):
    sessions = [Session(tag, logs).run() for tag in tags]
    sessions = [s for s in sessions if s.t0 is not None]
    return {
        "source": {
            "captures": [{"tag": s.tag, "character": s.self_name, "serial": f"0x{s.self_serial:08X}",
                          "in_region": [clock(s.t0), clock(s.t1)],
                          "packets_in_region": s.packets_in_region,
                          "pets": {n: f"0x{p:08X}" for p, n in sorted(s.pets.items())},
                          "other_players_seen": len(s.players_seen),
                          "red_players_seen": len(s.reds),
                          "player_armor_s": {str(a): round(v, 1) for a, v in sorted(s.armor.items())}}
                         for s in sessions],
            "scan": scanned,
            "generator": "harness/sim/calibrate.py",
        },
        "rules": {"region": f"facet {FACET}, x {X_RANGE[0]}-{X_RANGE[1]}, y {Y_RANGE[0]}-{Y_RANGE[1]}; only packets while "
                            "our player stood inside count.", **RULES},
        "damage_number_hues": damage_hues(sessions),
        "levels": levels(sessions),
        "creatures": creatures(sessions),
        **dict(zip(("pets", "party_damage_dealt"), pets_section(sessions))),
        "player": player_section(sessions),
        "bard": bard_section(sessions),
        "respawn": respawn_section(sessions),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tags", nargs="*", help="capture tags (default: the scenario's calibration.tags)")
    ap.add_argument("--scenario", default=DEFAULT_SCENARIO, help="its calibration block gives the region and tags")
    ap.add_argument("--logs", default=DEFAULT_LOGS)
    ap.add_argument("--out", help="default harness/data/sim/<scenario>_observed.json")
    ap.add_argument("--scan", metavar="YYYYMMDD", help="also include every session from this date on that visited the region")
    a = ap.parse_args(argv)
    default_tags = configure(a.scenario)
    a.out = a.out or os.path.join(SCENARIO_DIR, f"{a.scenario}_observed.json")
    tags = list(a.tags or default_tags)
    scanned = None
    if a.scan:
        scanned = scan(a.logs, a.scan)
        tags += [x["tag"] for x in scanned if x.get("packets_in_region") and x["tag"] not in tags]
    data = build(tags, a.logs, scanned)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, sort_keys=False)
        f.write("\n")
    print(json.dumps({"out": a.out, "tags": tags, "creature_types": len(data["creatures"])}))


if __name__ == "__main__":
    main()

"""Shy monsters' wakes in the live captures: how long from first sight of us to war mode and to the first act, and
whom they take (docs/NOTES.md "Shooters' and casters' first target: the delay and the nearest (2026-10-29)").

    python harness/sim/wake_scan.py [--logs DIR] [--since YYYYMMDD] [--tags T1,T2] [--out FILE] [--summary FILE]

Each capture is replayed through `WorldRuntime` in its row order (`replay.timed_packets`). While Logan Wolf stands in
the Mausoleum or Tidal Tomb (BOXES) it keeps every mobile's position (on each change), every monster's war flag (0x20 /
0x78 flags 0x40; Outlands' 0x77 carries none), the raw 0x6E animations, 0xC0 / 0x70 effects and 0x54 sounds, the '-N'
numbers over any mobile, and our C2S walks, speech ('All ...' commands) and target answers with their sender (`client`
= the user's hand, `agent` = the runner).

A **wake** is a shy monster's (aggro.shy) war flag going on after it was seen idle: from its first sighting, or after
IDLE_MIN_S idle (the war flag flaps on and off between casts in a fight: those aren't wakes). For each one, at every
change of its, Logan's or a pet's position from the idle start to WINDOW_S after the flip: the Chebyshev and Euclidean
distance of each of ours and its line of sight to them (los.Sight, the server's Map.LineOfSight: Window|NoShoot block;
also with Wall-flagged statics, `walls`; both eyes at z + 14 on the map surface, ground items left out). "Seen" is within
its aggro range (aggro.aggro_range: 10, 13 for the shadow minion and the calamity) and in sight; `seen14` the same at 14.

Its **acts** after the flip (from WAR_PRE_S before it), in packet order: a moving effect from it (0xC0/0x70 type 0,
source = it) at a mobile (`shot`, target serial); a volley of them at ground points (`volley`: one act, its victim the
mobile nearest the points' centroid); a fixed effect on a mobile (`fixed`: lightning, explosion, flamestrike), a '-N'
number (`number`) or the soulcatcher's 0x9FE on a mobile's tile (`sound`) within ATTR_S after its 0x6E, with no other
monster within 14 of the victim animating then, none within 1 of it, and no moving effect from anyone else at it.
Other spell sounds on our tiles were our caster pets' own casts. `first_act` is the earliest; `acts` all of them up
to WINDOW_S (retargets).

Ours: Logan (LOGAN) and his pets: notoriety 2 tame/bonded mobiles within 3 of him in PET_NEAR_P of their samples.
A row per wake and one per 'All ...' command (JSONL with --out). `--summary FILE` prints the NOTES tables from a rows
file: the reasons a wake is left out (`why`), the delay from the first of us within R_SIGHT and in sight to the flip,
the first single target against the nearest at the flip (`nearest_rel`, Euclidean, MARGIN), retargets, the user's
tactic cases and his 'All Patrol' pulls.
"""
import argparse
import bisect
import collections
import concurrent.futures
import glob
import json
import math
import os
import re
import sys

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HARNESS)
sys.path.insert(0, HARNESS)

LOGAN = 0x00104A60
BOXES = {"mausoleum": (5150, 600, 5650, 760), "tidal_tomb": (6600, 1900, 7401, 2225)}
MONSTER_NOTORIETY = (3, 4, 5, 6)
WAR = 0x40
IDLE_MIN_S = 10.0         # an idle stretch this long before a war flip makes it a wake (not the in-fight flapping)
WINDOW_S = 30.0           # acts and positions kept this long after the flip
WAR_PRE_S = 2.0           # acts this long before the flip still count (the flag rides the first act's packets)
ATTR_S = 3.0              # a fixed effect / sound / number this long after its 0x6E is its
VOLLEY_S = 0.6            # moving effects at ground points this close in time are one volley
LOOKBACK_S = 300.0        # sight looked for this far back in a long idle stretch at most
NEAR_R = 18               # Logan and a pet within this of it at the flip: a wake "near us"
GAP_S = 2.0               # an S2C silence this long in the wake's span: packets missing
PET_NEAR_P = 0.6
SIGHT14 = 14
NUMBER = re.compile(r"^-(\d+)$")
PET_WORDS = re.compile(r"^\s*all\s+(.*)$", re.IGNORECASE)
# fixed effects (0xC0 type 3) that are a monster's spell landing on its victim: explosion, flamestrike (not the others:
# a bard song's, a potion's or a bandage's land on Logan too). Sounds: only the soulcatcher's 0x9FE on its target's
# tile (docs/NOTES.md "Tidal Tomb: the zoas' lightning, ..."); the spell sounds (lightning 0x29, energy bolt 0x20A,
# magic arrow 0x1E5, mind blast 0x213) on our caster pets' own tiles were their casts (AlaFlambe 68 of 106 sound
# "victims", the searing imp's and fireminion's 0x1E5), not a monster's.
SPELL_FIXED = frozenset((0x36BD, 0x36B0, 0x3709))
SPELL_SOUNDS = frozenset((0x9FE,))


def _u16(p, o):
    return int.from_bytes(p[o:o + 2], "big")


def _u32(p, o):
    return int.from_bytes(p[o:o + 4], "big")


def _i32(p, o):
    return int.from_bytes(p[o:o + 4], "big", signed=True)


def _cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _eucl(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def place_of(x, y):
    for name, (x0, y0, x1, y1) in BOXES.items():
        if x is not None and y is not None and x0 <= x <= x1 and y0 <= y <= y1:
            return name
    return None


def direction_to(a, b):
    """The direction (0 north .. 7 north-west) from a to b by RunUO's Mobile.GetDirectionTo (the 44-unit isometric
    rule): what a monster turning to face b would send."""
    dx, dy = a[0] - b[0], a[1] - b[1]
    rx, ry = (dx - dy) * 44, (dx + dy) * 44
    ax, ay = abs(rx), abs(ry)
    if (ay >> 1) - ax >= 0:
        return 7 if ry > 0 else 3
    if (ax >> 1) - ay >= 0:
        return 5 if rx > 0 else 1
    if rx >= 0 and ry >= 0:
        return 6
    if rx >= 0 > ry:
        return 4
    if rx < 0 and ry < 0:
        return 2
    return 0


def _at(series, t):
    """The last entry of a time-sorted [(t, ...)] at or before t, else None."""
    i = bisect.bisect_right(series, (t, float("inf"))) - 1
    return series[i] if i >= 0 else None


def collect(packets):
    """The raw record of a capture while Logan stands in a dungeon box (module docstring)."""
    import threats
    from world.runtime import WorldRuntime
    now = [None]
    rt = WorldRuntime(clock=lambda: now[0])
    st = rt.state
    pos = collections.defaultdict(list)       # serial -> [(t, x, y, z, dir)]
    war = collections.defaultdict(list)       # serial -> [(t, war)] (first = first sighting)
    meta = {}                                 # serial -> {name, noto, pet, body}
    gone = collections.defaultdict(list)      # serial -> [t] (0x1D / 0xAF)
    anims = collections.defaultdict(list)     # serial -> [t]
    shots = []                                # (t, type, src, tgt, graphic, x, y, tx, ty)
    sounds = []                               # (t, sound, x, y)
    numbers = []                              # (t, serial, n)
    says, walks, aims = [], [], []            # (t, src, text) / (t, src) / (t, src, serial)
    s2c_t, places = [], []
    last_self = None
    for t, d, src, p in packets:
        now[0] = t
        rt.feed_packet(d, p)
        events = rt.drain_events()
        me = st.self
        where = place_of(me.x, me.y) if me.serial == LOGAN else None
        if not places or places[-1][1] != where:
            places.append((t, where))
        if where is None:
            last_self = None
            continue
        if (me.x, me.y, me.z) != last_self:
            last_self = (me.x, me.y, me.z)
            pos[LOGAN].append((t, me.x, me.y, me.z, me.direction))
        for e in events:
            if e["ev"] == "speech_heard" and (mm := NUMBER.match(e.get("text") or "")):
                numbers.append((t, e["serial"], int(mm.group(1))))
            elif e["ev"] == "speech" and d == "c2s":
                says.append((t, src, e.get("text") or ""))
            elif e["ev"] == "target_response" and d == "c2s" and e.get("serial"):
                aims.append((t, src, e["serial"]))
            elif e["ev"] == "mobile_death":
                gone[e["serial"]].append(t)
        if d == "c2s":
            if p[0] == 0x02:
                walks.append((t, src))
            continue
        s2c_t.append(t)
        c = p[0]
        if c in (0x20, 0x77, 0x78, 0xD3):
            s = _u32(p, 3) if c in (0x78, 0xD3) else _u32(p, 1)
            m = st.mobiles.get(s)
            if m is None or s == LOGAN or m.x is None:
                continue
            meta[s] = {"name": m.name, "noto": m.notoriety, "pet": m.pet, "body": m.graphic,
                       "human": m.graphic in threats.HUMAN_BODIES}
            q = (t, m.x, m.y, m.z, m.direction)
            if not pos[s] or pos[s][-1][1:] != q[1:]:
                pos[s].append(q)
            w = bool((m.flags or 0) & WAR)
            if not war[s] or war[s][-1][1] != w:
                war[s].append((t, w))
        elif c == 0x1D and len(p) >= 5:
            gone[_u32(p, 1)].append(t)
        elif c == 0x6E and len(p) >= 14:
            anims[_u32(p, 1)].append(t)
        elif c == 0xC0 and len(p) >= 52:
            shots.append((t, p[1], _u32(p, 2), _u32(p, 6), _u32(p, 10), _u32(p, 14), _u32(p, 18), _u32(p, 26),
                          _u32(p, 30)))
        elif c == 0x70 and len(p) >= 28:
            shots.append((t, p[1], _u32(p, 2), _u32(p, 6), _u16(p, 10), _u16(p, 12), _u16(p, 14), _u16(p, 17),
                          _u16(p, 19)))
        elif c == 0x54 and len(p) >= 18:
            sounds.append((t, _u16(p, 2), _u32(p, 6), _u32(p, 10)))
    return {"pos": pos, "war": war, "meta": meta, "gone": gone, "anims": anims, "shots": shots, "sounds": sounds,
            "numbers": numbers, "says": says, "walks": walks, "aims": aims, "s2c_t": s2c_t, "places": places}


def our_pets(rec):
    """Logan's pets: notoriety 2 tame/bonded mobiles within 3 of him in PET_NEAR_P of their position samples."""
    logan = rec["pos"][LOGAN]
    out = {}
    for s, m in rec["meta"].items():
        if m["noto"] != 2 or m["pet"] not in ("tame", "bonded") or m["human"]:
            continue
        samples = rec["pos"][s]
        near = sum(1 for q in samples if (lq := _at(logan, q[0])) is not None and _cheb(q[1:3], lq[1:3]) <= 3)
        if samples and near / len(samples) >= PET_NEAR_P and len(samples) >= 20:
            out[s] = m["name"]
    return out


def wakes(rec, pets, sight, wall_sight, walk):
    import aggro
    import los

    from sim.calibrate import type_key
    ours = [LOGAN, *pets]
    pos, war, meta = rec["pos"], rec["war"], rec["meta"]
    known = aggro.creatures()
    # a monster: notoriety 3-6 and a creature kind the scenarios know, or not a human body (players are human bodies;
    # the drowned mariners and the reanimators are human-bodied monsters)
    monsters = {s for s, m in meta.items() if m["noto"] in MONSTER_NOTORIETY
                and (type_key(m["name"]) in known or not m["human"])}
    anim_all = sorted((t, s) for s in monsters for t in rec["anims"].get(s, ()))
    anim_ts = [a[0] for a in anim_all]
    s2c_t = rec["s2c_t"]
    zc = {}

    def z3(q):
        k = (q[1], q[2], q[3])
        if k not in zc:
            zc[k] = los.surface_z(walk, q[1], q[2], q[3] or 0)
        return (q[1], q[2], zc[k])

    lc, wc = {}, {}

    def sees(a, b, strict=False):
        c = wc if strict else lc
        k = (a, b)
        if k not in c:
            c[k] = (wall_sight if strict else sight).can_see(a, b)
        return c[k]

    def where(s, t):
        q = _at(pos.get(s, ()), t)
        if q is None:
            return None
        g = rec["gone"].get(s)
        if g:
            i = bisect.bisect_right(g, t) - 1
            if i >= 0 and g[i] >= q[0]:
                return None          # deleted or dead since its last position
        return q

    def state(m, t, r):
        """{serial: {d, e, los, walls, seen, seen14}} of ours with a position at t, the monster's tile."""
        mq = where(m, t)
        if mq is None:
            return None, {}
        mp = z3(mq)
        out = {}
        for s in ours:
            q = where(s, t)
            if q is None:
                continue
            up = z3(q)
            d = _cheb(mp, up)
            v = d <= los.MAX_DISTANCE and sees(mp, up)
            out[s] = {"d": d, "e": round(_eucl(mp, up), 2), "los": v,
                      "walls": v and sees(mp, up, True) if d <= SIGHT14 + 4 else False,
                      "seen": v and d <= r, "seen14": v and d <= SIGHT14, "pos": up[:2], "dir": direction_to(mp, up)}
        return mp[:2], out

    def others_near(m, t):
        """[(d, serial)] of the bystanders (not ours, not monsters: other players and their pets) within SIGHT14 of
        monster m at t and in its sight, nearest first."""
        mq = where(m, t)
        if mq is None:
            return []
        mp = z3(mq)
        out = []
        for s in bystanders:
            q = where(s, t)
            if q is not None and (d := _cheb(q[1:3], mq[1:3])) <= SIGHT14 and sees(mp, z3(q)):
                out.append((d, s))
        return sorted(out)

    bystanders = [s for s in meta if s not in monsters and s not in ours]
    shot_ts = [sh[0] for sh in rec["shots"]]
    num_ts = [n[0] for n in rec["numbers"]]
    snd_ts = [x[0] for x in rec["sounds"]]

    def victims_near(x, y, t, exclude, r=1):
        """[(d, serial)] of the mobiles (ours and bystanders) within r of (x, y) at t, nearest first."""
        out = []
        for s in (*ours, *bystanders):
            if s in exclude:
                continue
            q = where(s, t)
            if q is not None and (d := _cheb(q[1:3], (x, y))) <= r:
                out.append((d, s))
        return sorted(out)

    def acts(m, t0, t1, near):
        """[(t, victim, channel, extra)] by monster m in [t0, t1] in packet order (module docstring); `near`: the
        monsters around it. A volley of moving effects at ground points (the corpse purger's) is one act at its
        first effect, its victim the mobile nearest the points' centroid (extra: centroid, that distance, the next's)."""
        out = []
        mine = rec["anims"].get(m, [])
        shots = rec["shots"]
        volley = []

        def flush():
            if not volley:
                return
            cx = sum(p[1] for p in volley) / len(volley)
            cy = sum(p[2] for p in volley) / len(volley)
            vs = victims_near(round(cx), round(cy), volley[0][0], {m}, r=3)
            if vs:
                out.append((volley[0][0], vs[0][1], "volley",
                            {"n": len(volley), "c": [round(cx, 1), round(cy, 1)], "d": vs[0][0],
                             "d2": vs[1][0] if len(vs) > 1 else None}))
            volley.clear()

        for sh in shots[bisect.bisect_left(shot_ts, t0):bisect.bisect_right(shot_ts, t1)]:
            t, typ, src, tgt = sh[0], sh[1], sh[2], sh[3]
            if volley and t - volley[-1][0] > VOLLEY_S:
                flush()
            if typ == 0 and src == m:
                if tgt and tgt != m and (tgt in meta or tgt == LOGAN):
                    out.append((t, tgt, "shot", None))
                else:
                    volley.append((t, sh[7], sh[8]))
            elif (typ == 1 or (typ == 3 and sh[4] in SPELL_FIXED)) and src and src not in monsters \
                    and _attributed(m, mine, src, t, near):
                out.append((t, src, "fixed", sh[4]))
        flush()
        nums = rec["numbers"]
        for t, s, n in nums[bisect.bisect_left(num_ts, t0):bisect.bisect_right(num_ts, t1)]:
            if s not in monsters and _attributed(m, mine, s, t, near):
                out.append((t, s, "number", n))
        snds = rec["sounds"]
        for t, snd, x, y in snds[bisect.bisect_left(snd_ts, t0):bisect.bisect_right(snd_ts, t1)]:
            if snd in SPELL_SOUNDS:
                for _d, v in victims_near(x, y, t, {m}, r=0):
                    if _attributed(m, mine, v, t, near):
                        out.append((t, v, "sound", snd))
        out.sort(key=lambda a: a[0])
        return out

    def _attributed(m, mine, victim, t, near):
        """Whether a fixed effect / sound / number on `victim` at t is monster m's: its 0x6E within ATTR_S before,
        it within SIGHT14 of the victim, no other monster within 14 of the victim animating then, none within 1 of the
        victim, and no moving effect from anyone else at the victim then."""
        i = bisect.bisect_right(mine, t)
        if i == 0 or t - mine[i - 1] > ATTR_S:
            return False
        vq, mq = where(victim, t), where(m, t)
        if vq is None or mq is None or _cheb(vq[1:3], mq[1:3]) > SIGHT14:
            return False
        j, k = bisect.bisect_left(anim_ts, t - ATTR_S), bisect.bisect_right(anim_ts, t)
        for ta, s in anim_all[j:k]:
            if s != m and (sq := where(s, ta)) is not None and _cheb(sq[1:3], vq[1:3]) <= 14:
                return False
        for s in near:
            if s != m and (sq := where(s, t)) is not None and _cheb(sq[1:3], vq[1:3]) <= 1:
                return False
        shots = rec["shots"]
        for sh in shots[bisect.bisect_left(shot_ts, t - ATTR_S):bisect.bisect_right(shot_ts, t)]:
            if sh[1] == 0 and sh[3] == victim and sh[2] != m:
                return False
        return True

    rows = []
    shy_bodies = {meta[s]["body"] for s in monsters if aggro.shy(type_key(meta[s]["name"]))}
    for m in monsters:
        kind = type_key(meta[m]["name"])
        if not war.get(m):
            continue
        series = war[m]
        if not meta[m]["name"]:
            if meta[m]["body"] in shy_bodies and any(w for _t, w in series):
                rows.append({"serial": m, "kind": None, "body": meta[m]["body"], "unnamed": True,
                             "t_war": next(t for t, w in series if w), "place": _place_at(rec["places"], series[0][0])})
            continue
        if not aggro.shy(kind):
            continue
        r = aggro.aggro_range(kind)
        for i, (tw, w) in enumerate(series):
            if not w:
                continue
            if i == 0:                        # first seen awake: no wake to time
                lq, mq = where(LOGAN, tw), where(m, tw)
                if lq is not None and mq is not None and _cheb(lq[1:3], mq[1:3]) <= NEAR_R:
                    rows.append({"serial": m, "kind": kind, "t_war": tw, "awake_at_sight": True,
                                 "place": _place_at(rec["places"], tw)})
                continue
            t_idle = series[i - 1][0]
            first = i == 1
            if not first and tw - t_idle < IDLE_MIN_S:
                continue
            row = {"serial": m, "kind": kind, "r": r, "t_war": tw, "t_idle": t_idle, "idle_s": round(tw - t_idle, 2),
                   "first_sighting": first, "place": _place_at(rec["places"], tw)}
            lq, mq = where(LOGAN, tw), where(m, tw)
            if lq is None or mq is None or _cheb(lq[1:3], mq[1:3]) > NEAR_R:
                row["far"] = True
                rows.append(row)
                continue
            # every change point of its, Logan's and the pets' positions in [t_start, tw + WINDOW_S]
            t_start = max(t_idle, tw - LOOKBACK_S)
            t_end = tw + WINDOW_S
            g = rec["gone"].get(m)
            if g:
                k = bisect.bisect_right(g, tw)
                if k < len(g):
                    t_end = min(t_end, g[k])
            pts = {t_start, tw}
            for s in (m, *ours):
                sr = pos.get(s, ())
                j = bisect.bisect_left(sr, (t_start,))
                while j < len(sr) and sr[j][0] <= t_end:
                    pts.add(sr[j][0])
                    j += 1
            pts = sorted(pts)
            near = [s for s in monsters if s != m and (q := where(s, tw)) is not None
                    and _cheb(q[1:3], mq[1:3]) <= 30]
            tl = []
            for t in pts:
                mp, ss = state(m, t, r)
                if mp is not None:
                    tl.append((t, mp, ss))
            if not tl:
                continue
            # first sights in the idle stretch
            first_seen = {}
            first_seen14 = {}
            edge = {}
            prev = {}
            for t, mp, ss in tl:
                if t > tw:
                    break
                for s, v in ss.items():
                    if v["seen"] and s not in first_seen:
                        first_seen[s] = t
                        pv = prev.get(s)
                        edge[s] = None if pv is None else ("los" if pv["d"] <= r else "range")
                    if v["seen14"] and s not in first_seen14:
                        first_seen14[s] = t
                    prev[s] = v
            t_first_eval = tl[0][0]
            row["t_logan_sight"] = first_seen.get(LOGAN)
            row["t_pet_sight"] = {f"0x{s:08X}": first_seen[s] for s in pets if s in first_seen}
            row["t_logan_sight14"] = first_seen14.get(LOGAN)
            row["t_any_sight14"] = min(first_seen14.values()) if first_seen14 else None
            row["censored"] = any(t == t_first_eval for t in first_seen.values())
            row["logan_edge"] = edge.get(LOGAN)
            # who moved into sight: the onset tick's mover
            row["t_any_sight"] = min(first_seen.values()) if first_seen else None
            row["first_seer"] = (f"0x{min(first_seen, key=first_seen.get):08X}" if first_seen else None)
            # sight held from Logan's onset to the flip?
            if LOGAN in first_seen:
                held = all(ss.get(LOGAN, {}).get("seen") for t, _mp, ss in tl if first_seen[LOGAN] <= t <= tw)
                row["logan_held"] = held
            _mp, sw = state(m, tw, r)
            row["at_war"] = _fmt(sw)
            row["mon_war_pos"] = list(_mp) if _mp else None
            act = acts(m, tw - WAR_PRE_S, t_end, near)
            row["acts"] = [[round(a[0], 3), f"0x{a[1]:08X}", a[2], a[3]] for a in act]
            row["first_act"] = row["acts"][0] if act else None
            # its facing (0x77/0x20 direction, low 3 bits) from 1 s before the flip to 3 s after
            row["facing"] = [[round(q[0] - tw, 3), (q[4] or 0) & 7] for q in pos.get(m, ())
                             if tw - 1.0 <= q[0] <= tw + 3.0]
            fq = where(m, tw - 1e-3)
            row["facing_before"] = (fq[4] or 0) & 7 if fq else None
            if act:
                ta = act[0][0]
                _mp, sa = state(m, ta, r)
                row["at_act"] = _fmt(sa)
                row["mon_act_pos"] = list(_mp) if _mp else None
            row["first_anim"] = next((round(t, 3) for t in rec["anims"].get(m, ()) if t >= tw - WAR_PRE_S), None)
            # who hit or aimed at it before the flip (woken by an attack, not by sight)
            nums = rec["numbers"]
            row["hit_before"] = any(s == m for _t, s, _n in
                                    nums[bisect.bisect_left(num_ts, t_idle):bisect.bisect_left(num_ts, tw)])
            shots = rec["shots"]
            row["shot_at_before"] = any(sh[1] == 0 and sh[3] == m and sh[2] in ours for sh in
                                        shots[bisect.bisect_left(shot_ts, t_idle):bisect.bisect_left(shot_ts, tw)])
            row["aimed_before"] = sorted({src for t, src, s in rec["aims"] if s == m and t_idle <= t < tw})
            onset = row["t_any_sight"] or tw
            row["others_near"] = {k: [[d, f"0x{s:08X}"] for d, s in others_near(m, t)] for k, t in
                                  (("onset", onset), ("war", tw), ("act", act[0][0] if act else None)) if t is not None}
            lo, hi = onset - 2.0, (act[0][0] if act else tw) + 1.0
            j = bisect.bisect_left(s2c_t, lo)
            gap = 0.0
            while j + 1 < len(s2c_t) and s2c_t[j] <= hi:
                gap = max(gap, s2c_t[j + 1] - s2c_t[j])
                j += 1
            row["max_gap_s"] = round(gap, 2)
            w0, w1 = onset - 10.0, tw + 10.0
            ws = collections.Counter(src for t, src in rec["walks"] if w0 <= t <= w1)
            row["walks"] = dict(ws)
            row["driver"] = ("runner" if ws.get("agent") and not ws.get("client") else
                             "user" if ws.get("client") and not ws.get("agent") else
                             "mixed" if ws else "still")
            cmd = [(t, src, txt) for t, src, txt in rec["says"] if PET_WORDS.match(txt) and t <= tw]
            row["pet_cmd"] = ([round(tw - cmd[-1][0], 2), cmd[-1][1], cmd[-1][2]] if cmd else None)
            row["pet_cmds_after"] = [[round(t - tw, 2), src, txt] for t, src, txt in rec["says"]
                                     if PET_WORDS.match(txt) and tw < t <= tw + 10]
            row["timeline"] = [[round(t, 3), list(mp), _fmt(ss)] for t, mp, ss in tl
                               if onset - 5.0 <= t <= (act[0][0] if act else tw) + 20.0]
            rows.append(row)
    return rows


def _fmt(ss):
    return {("logan" if s == LOGAN else f"0x{s:08X}"): {k: v for k, v in x.items()} for s, x in ss.items()}


def _place_at(places, t):
    i = bisect.bisect_right([p[0] for p in places], t) - 1
    return places[i][1] if i >= 0 else None


def scan(job):
    import los
    import pathfind
    import replay
    logs, tag = job
    try:
        packets = replay.timed_packets(os.path.join(logs, f"session_{tag}"))
        rec = collect(packets)
    except Exception as e:                       # noqa: BLE001 (a capture that doesn't replay is skipped, named)
        return tag, [{"tag": tag, "error": str(e)[:200]}]
    if not rec["pos"].get(LOGAN):
        return tag, []
    walk = pathfind.Walkers().get(0)
    pets = our_pets(rec)
    rows = wakes(rec, pets, los.Sight(walk), los.Sight(walk, walls=True), walk)
    for r in rows:
        r["tag"] = tag
        r["pets"] = {f"0x{s:08X}": n for s, n in pets.items()}
    rows += [{"tag": tag, "cmd": text, "t": t, "src": src} for t, src, text in rec["says"] if PET_WORDS.match(text)]
    return tag, rows


# ------------------------------------------------------------------ summary (the NOTES tables)
R_SIGHT = 10              # the sight onset the tables use for every type (the shadow minion's and calamity's 13 too:
#                           their delays are tightest from 10, and every one of their wakes had one of us within 10)
MARGIN = 1.0              # "nearer" in the tables: by at least this many tiles (Euclidean, RunUO's GetDistanceToSqrt)
PROMPT_S = 3.5            # a first act within this of the flip: its first target (later ones may follow a retarget)


def why(r):
    """'ok' for a wake the tables use, else the reason it is left out."""
    if r.get("unnamed"):
        return "unnamed (no name packet: type unknown)"
    if r.get("awake_at_sight"):
        return "already awake when first seen"
    if r.get("far") or "logan" not in r.get("at_war", {}):
        return "Logan beyond 18 at the flip"
    if not any(k != "logan" and v["d"] <= NEAR_R for k, v in r["at_war"].items()):
        return "no pet within 18"
    if r["hit_before"] or r["shot_at_before"] or r["aimed_before"]:
        return "hit, shot or targeted by us before the flip"
    if r["t_any_sight"] is None:
        return "none of us in its range and sight before the flip (our map)"
    if r["censored"]:
        return "one of us in its sight already when first seen"
    if r["max_gap_s"] > GAP_S:
        return "packets missing (S2C gap > 2 s)"
    on, wa = r["others_near"].get("onset", []), r["others_near"].get("war", [])
    ours = [x["d"] for x in r["at_war"].values() if x["los"] and x["d"] <= SIGHT14]
    if on or (wa and (not ours or wa[0][0] <= min(ours))):
        return "a bystander (another player or pet) in its sight first or nearer"
    return "ok"


def _state_at(r, t):
    best = None
    for e in r["timeline"]:
        if e[0] > t + 1e-9:
            break
        best = e
    return best[2] if best else None


def _onset(r, who=None):
    """The first timeline time at or before the flip with one of ours (or `who`) within R_SIGHT and in its sight."""
    for t, _mp, ss in r["timeline"]:
        if t > r["t_war"] + 1e-6:
            break
        if any(v["los"] and v["d"] <= R_SIGHT for k, v in ss.items() if who is None or k == who):
            return t
    return None


def prepare(r):
    """Derived fields: onsets at R_SIGHT, delays, the first act's victims and its single target (`clean`)."""
    tw = r["t_war"]
    r["on"] = _onset(r)
    r["onL"] = _onset(r, "logan")
    r["dW"] = tw - r["on"] if r["on"] is not None else None
    r["dWL"] = tw - r["onL"] if r["onL"] is not None else None
    r["dAnim"] = r["first_anim"] - r["on"] if r["first_anim"] is not None and r["on"] is not None else None
    r["held"] = r["on"] is not None and all(any(v["los"] and v["d"] <= R_SIGHT for v in e[2].values())
                                            for e in r["timeline"] if r["on"] <= e[0] <= tw)
    st0, st1 = _state_at(r, r["on"] - 1e-3) if r["on"] else None, _state_at(r, r["on"]) if r["on"] else None
    r["seers"] = [k for k, v in (st1 or {}).items() if v["los"] and v["d"] <= R_SIGHT]
    prev = [(st0 or {}).get(k) for k in r["seers"]]
    r["edge"] = ("los" if any(p and p["d"] <= R_SIGHT and not p["los"] for p in prev) else
                 "range" if any(prev) else None)
    r["clean"] = r["t_act"] = r["ch"] = None
    r["groups"] = []
    for a in r["acts"]:
        v = "logan" if a[1] == f"0x{LOGAN:08X}" else a[1]
        if r["groups"] and a[0] - r["groups"][-1][0] <= 0.1:
            if v not in r["groups"][-1][1]:
                r["groups"][-1][1].append(v)
            continue
        r["groups"].append([a[0], [v], a[2], a[3]])
    if r["groups"]:
        t0, vs, ch, ex = r["groups"][0]
        r["t_act"], r["ch"] = t0, ch
        r["dA"] = t0 - r["on"] if r["on"] is not None else None
        unique = ch != "volley" or ex["d2"] is None or ex["d2"] > ex["d"]
        if len(vs) == 1 and unique and (vs[0] == "logan" or vs[0] in r["at_war"]):
            r["clean"] = vs[0]
    return r


def nearest_rel(st, v, margin=MARGIN, key="e"):
    """(how the victim v stood at state st: nearest/farther/tie/alone/unseen, the nearest of ours in sight)."""
    if not st or v not in st:
        return "unseen", None
    ins = {k: x for k, x in st.items() if x["los"] and x["d"] <= SIGHT14}
    if v not in ins:
        return "unseen", None
    if len(ins) == 1:
        return "alone", v
    ds = sorted((x[key], k) for k, x in ins.items())
    if ds[1][0] - ds[0][0] < margin:
        return "tie", None
    return ("nearest" if ds[0][1] == v else "farther"), ds[0][1]


def pct(values, ps=(0.05, 0.10, 0.25, 0.5, 0.75, 0.9)):
    v = sorted(x for x in values if x is not None)
    if not v:
        return "-"
    out = []
    for p in ps:
        k = (len(v) - 1) * p
        f = math.floor(k)
        c = min(len(v) - 1, f + 1)
        out.append(f"{v[f] + (v[c] - v[f]) * (k - f):.2f}")
    return " ".join(out)


def summary(rows):
    """Print the tables of docs/NOTES.md "Shooters' and casters' first target ..." from scan rows."""
    cmds = [r for r in rows if "cmd" in r]
    rows = [r for r in rows if "cmd" not in r and "error" not in r]
    for r in rows:
        r["why"] = why(r)
    ok = [prepare(r) for r in rows if r["why"] == "ok"]
    print(f"## wakes: {len(rows)} shy-monster war flips after idle; {len(ok)} used")
    for k, n in collections.Counter(r["why"] for r in rows).most_common():
        places = collections.Counter(r.get("place") for r in rows if r["why"] == k)
        print(f"  {n:4d}  {k}  ({', '.join(f'{p} {c}' for p, c in places.items())})")

    print(f"\n## delay from first sight (one of us within {R_SIGHT} and in its sight) to the war flip, s: "
          "p5 p10 p25 p50 p75 p90")
    sets = [("all", ok), ("sight held to the flip", [r for r in ok if r["held"]]),
            ("onset by a step into range", [r for r in ok if r["edge"] == "range"]),
            ("onset by a step into line of sight", [r for r in ok if r["edge"] == "los"]),
            ("Mausoleum", [r for r in ok if r["place"] == "mausoleum"]),
            ("Tidal Tomb", [r for r in ok if r["place"] == "tidal_tomb"]),
            ("runner driving", [r for r in ok if r["driver"] == "runner"]),
            ("user driving", [r for r in ok if r["driver"] == "user"])]
    for name, s in sets:
        print(f"  {name:36s} n {len(s):3d}  {pct(r['dW'] for r in s)}")
    print(f"  {'from Logan’s own sight':36s} n {sum(r['dWL'] is not None for r in ok):3d}  {pct(r['dWL'] for r in ok)}")
    print(f"  {'to its first 0x6E (cast/swing begin)':36s} n {sum(r['dAnim'] is not None for r in ok):3d}  "
          f"{pct(r['dAnim'] for r in ok)}")
    print(f"  {'to its first act landing on a mobile':36s} n {sum(r.get('dA') is not None for r in ok):3d}  "
          f"{pct(r.get('dA') for r in ok)}")
    held = sorted(r["dW"] for r in ok if r["held"])
    print(f"  held: under 1.0 s {sum(x < 1.0 for x in held)}, under 1.5 s {sum(x < 1.5 for x in held)}, under 1.6 s "
          f"{sum(x < 1.6 for x in held)} of {len(held)}")
    walled = [r for r in ok if r["on"] is not None and not any(
        v["walls"] for k, v in _state_at(r, r["on"]).items() if k in r["seers"])]
    print(f"  onsets only through a Wall-flagged static (los.Sight walls=True would block): {len(walled)}, "
          f"their delay {pct((r['dW'] for r in walled), (.1, .5, .9))}")
    by = collections.defaultdict(list)
    for r in ok:
        by[r["kind"]].append(r)
    print("  per type (n >= 8): n | sight->war p5 p10 p25 p50 p75 p90 | held p10 p50 p90 | ->first act p10 p50")
    rest = []
    for k, s in sorted(by.items(), key=lambda x: -len(x[1])):
        if len(s) < 8:
            rest += s
            continue
        print(f"  {k:20s} {len(s):3d} | {pct(r['dW'] for r in s)} | {pct((r['dW'] for r in s if r['held']), (.1, .5, .9))}"
              f" | {pct((r.get('dA') for r in s), (.1, .5))}")
    if rest:
        print(f"  {'(others pooled)':20s} {len(rest):3d} | {pct(r['dW'] for r in rest)} | "
              f"{pct((r['dW'] for r in rest if r['held']), (.1, .5, .9))} | {pct((r.get('dA') for r in rest), (.1, .5))}")
    for r13 in sorted({r["r"] for r in ok if r["r"] != R_SIGHT}):
        s = [r for r in ok if r["r"] == r13]
        print(f"  range check, the types aggro.aggro_range puts at {r13} (n {len(s)}): delay IQR from first within R:")
        for rr in (r13, 12, 11, 10, 9, 8):
            d, never = [], 0
            for r in s:
                t = next((e[0] for e in r["timeline"] if e[0] <= r["t_war"] + 1e-6 and
                          any(v["los"] and v["d"] <= rr for v in e[2].values())), None)
                if t is None:
                    never += 1
                else:
                    d.append(r["t_war"] - t)
            print(f"    R {rr:2d}: p25 p50 p75 {pct(d, (.25, .5, .75))}; none of us within R before the flip {never}")

    clean = [r for r in ok if r["clean"]]
    prompt = [r for r in clean if r["t_act"] - r["t_war"] <= PROMPT_S]
    print(f"\n## first target against the nearest at the flip (in its sight within {SIGHT14}); single-target first acts "
          f"{len(clean)} of {sum(1 for r in ok if r['groups'])} with an act ({len(prompt)} within {PROMPT_S} s of the flip)")
    print("  channel of the first act:", dict(collections.Counter(r["ch"] for r in clean)))
    bands = ("pet nearer by >=2", "pet nearer by 1-2", "pet nearer by <1", "equal", "Logan nearer by <1",
             "Logan nearer by 1-2", "Logan nearer by >=2")
    for name, s in (("all", clean), (f"first act within {PROMPT_S} s", prompt)):
        for key, kn in (("e", "Euclidean"), ("d", "Chebyshev")):
            t = collections.Counter()
            for r in s:
                st, v = r["at_war"], r["clean"]
                ins = {k: x for k, x in st.items() if x["los"] and x["d"] <= SIGHT14}
                if v not in ins or "logan" not in ins or len(ins) < 2:
                    continue
                diff = ins["logan"][key] - min(x[key] for k, x in ins.items() if k != "logan")
                b = bands[0 if diff >= 2 else 1 if diff >= 1 else 2 if diff > 0 else 3 if diff == 0 else
                          4 if diff > -1 else 5 if diff > -2 else 6]
                t[(b, v == "logan")] += 1
            print(f"  {name}, {kn}: (Logan taken / a pet taken) " +
                  "; ".join(f"{b} {t[(b, True)]}/{t[(b, False)]}" for b in bands))
    for name, s in (("all", clean), (f"within {PROMPT_S} s", prompt)):
        for when in ("at_war", "at_act"):
            c = collections.Counter(nearest_rel(r.get(when), r["clean"])[0] for r in s)
            dec = [r for r in s if nearest_rel(r.get(when), r["clean"])[0] in ("nearest", "farther")]
            chance = sum(1 / sum(1 for x in r[when].values() if x["los"] and x["d"] <= SIGHT14) for r in dec)
            print(f"  {name} {when}: {dict(c)}; decisive {len(dec)}, nearest taken {c['nearest']} "
                  f"(chance {chance:.1f})")
    print("  per type (all, at the flip): nearest/farther/tie/alone/unseen")
    byc = collections.defaultdict(collections.Counter)
    for r in clean:
        byc[r["kind"]][nearest_rel(r["at_war"], r["clean"])[0]] += 1
    for k, c in sorted(byc.items(), key=lambda x: -sum(x[1].values())):
        print(f"    {k:20s} " + " ".join(f"{c[x]}" for x in ("nearest", "farther", "tie", "alone", "unseen")))
    t = collections.Counter()
    for r in clean:
        rel, near = nearest_rel(r["at_war"], r["clean"])
        if rel not in ("nearest", "farther") or not r["seers"]:
            continue
        if len(r["seers"]) == 1 and r["seers"][0] != near:
            got = "the nearest" if r["clean"] == near else "the first seen" if r["clean"] == r["seers"][0] else "a third"
            t[f"{'Logan' if r['seers'][0] == 'logan' else 'a pet'} seen first, "
              f"{'Logan' if near == 'logan' else 'a pet'} nearest at the flip: took {got}"] += 1
        elif near in r["seers"]:
            t[f"the nearest at the flip among the first seen: {'took it' if r['clean'] == near else 'took another'}"] += 1
    print("  first seen against nearest at the flip:", dict(t))

    print("\n## the user's tactic: Logan alone seen first, then a pet the nearest of us in its sight at the flip "
          "(by >= 1, or Logan out of its sight)")
    t = collections.Counter()
    for r in clean:
        if r["seers"] != ["logan"]:
            continue
        rel, near = nearest_rel(r["at_war"], r["clean"])
        took = "Logan" if r["clean"] == "logan" else "a pet"
        at = "no clear nearest" if near is None else "Logan nearest" if near == "logan" else "a pet nearest"
        t[f"Logan seen first; at the flip {at}: took {took}"] += 1
        if near not in (None, "logan"):
            st0, lw = _state_at(r, r["on"]), r["at_war"]["logan"]
            print(f"  {_ts(r['t_war'])} {r['tag']} {r['kind']} ({r['driver']}, pets "
                  f"'{(r['pet_cmd'] or [0, 0, '-'])[2]}'): Logan seen at {st0['logan']['d']}; at the flip Logan "
                  f"{lw['e']}{'' if lw['los'] else ' out of its sight'}, {near} {r['at_war'][near]['e']}; "
                  f"{r['dW']:.2f} s{'' if r['held'] else ' (sight broken)'}; took {r['clean']} ({r['ch']}, "
                  f"+{r['t_act'] - r['t_war']:.2f} s)")
    for k, n in sorted(t.items()):
        print(f"  {n:4d}  {k}")

    print("\n## retargets (single-target acts over the next 30 s)")
    t, after = collections.Counter(), collections.defaultdict(list)
    for r in clean:
        g = [x for x in r["groups"] if len(x[1]) == 1]
        if len(g) < 2:
            t["fewer than two single-target acts"] += 1
            continue
        v0 = g[0][1][0]
        nxt = next((x for x in g[1:] if x[1][0] != v0), None)
        if nxt is None:
            t["kept its first target"] += 1
            continue
        v1 = nxt[1][0]
        st = _state_at(r, nxt[0] - 0.5)
        rel1, _n = nearest_rel(st, v1)
        rel0, _n = nearest_rel(st, v0)
        lab = ("to the new nearest" if rel1 in ("nearest", "alone") else "away from the nearest (old one)"
               if rel0 == "nearest" else "no clear nearest")
        kind = f"{'Logan' if v0 == 'logan' else 'pet'}->{'Logan' if v1 == 'logan' else 'pet'}"
        t[f"switched {kind} {lab}"] += 1
        after[lab].append(nxt[0] - g[0][0])
    for k, n in sorted(t.items()):
        print(f"  {n:4d}  {k}")
    for lab, d in after.items():
        print(f"  {lab}: after the first act p10 p50 p90 {pct(d, (.1, .5, .9))} s")

    print("\n## the user's own 'All Patrol' pulls (C2S from the client within 30 s before the flip)")
    for r in sorted(rows, key=lambda r: r["t_war"]):
        pc = r.get("pet_cmd")
        if not pc or pc[1] != "client" or "patrol" not in pc[2].lower() or pc[0] > 30:
            continue
        first = (r.get("clean") or (r["acts"][0][1] if r.get("acts") else None)) if r["why"] == "ok" else None
        w = r.get("at_war") or {}
        near = min(((x["e"], k) for k, x in w.items() if x["los"] and x["d"] <= SIGHT14), default=(None, None))
        print(f"  {_ts(r['t_war'])} {r['tag']} {r['kind']}: patrol {pc[0]:.1f} s before; {r['why']}; "
              f"L at {w.get('logan', {}).get('e')}, nearest {near[1]} {near[0]}; first act on {first}")
    by_tag = collections.defaultdict(list)
    for c in cmds:
        by_tag[c["tag"]].append(c)
    for tag, cs in sorted(by_tag.items()):
        pats = [c for c in cs if c["src"] == "client" and "patrol" in c["cmd"].lower()]
        if not pats:
            continue
        back = [next((round(f["t"] - p["t"], 1) for f in cs if f["src"] == "client" and p["t"] < f["t"] <= p["t"] + 30
                      and "follow" in f["cmd"].lower()), None) for p in pats]
        print(f"  {tag}: {len(pats)} 'All Patrol' by hand; an 'All Follow Me' within 30 s after {sum(b is not None for b in back)}"
              f" ({', '.join(f'{b} s' for b in back if b is not None)})")


def _ts(t):
    import datetime
    return datetime.datetime.fromtimestamp(t).strftime("%m-%d %H:%M:%S")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--logs", default=os.path.join(REPO, "logs"))
    ap.add_argument("--since", default="20261015")
    ap.add_argument("--tags", default=None, help="comma-separated capture tags instead of --since")
    ap.add_argument("--out", default=None, help="write the rows here (JSONL)")
    ap.add_argument("--summary", default=None, metavar="FILE", help="print the tables from a rows file; no scan")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    a = ap.parse_args(argv)
    if a.summary:
        with open(a.summary, encoding="utf-8") as f:
            summary([json.loads(line) for line in f])
        return 0
    if a.tags:
        tags = a.tags.split(",")
    else:
        tags = sorted(os.path.basename(p)[8:-6] for p in glob.glob(os.path.join(a.logs, "session_*.jsonl")))
        tags = [t for t in tags if t[:8] >= a.since and os.path.exists(os.path.join(a.logs, f"session_{t}.s2c.raw"))]
    rows_all = []
    with concurrent.futures.ProcessPoolExecutor(a.jobs) as ex:
        for tag, rows in ex.map(scan, [(a.logs, t) for t in tags]):
            n = sum(1 for r in rows if "cmd" not in r)
            print(f"{tag}: {n} wakes" + (f" ({rows[0]['error']})" if rows and "error" in rows[0] else ""),
                  file=sys.stderr)
            rows_all += rows
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows_all)
    return 0


if __name__ == "__main__":
    sys.exit(main())

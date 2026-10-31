"""What a forgotten soul does in Logan Wolf's live Mausoleum fights, against the shadowguard and the vampire countess.

    python harness/sim/soul_scan.py [--logs DIR] [--since YYYYMMDD] [--out DIR] [--summary]

Each capture is replayed through `WorldRuntime` in its row order (`replay.timed_packets`); only packets while Logan
Wolf (LOGAN) stands in the Mausoleum (sim.calibrate.in_region) count. Kept: every mobile's tile (0x20/0x77/0x78/0xD3),
each monster's war flag (0x20/0x78 flags 0x40), deaths and deletes (0xAF, 0x1D, prunes), the raw 0x6E animations
(with the action), 0xC0/0x70 effects and the '-N' numbers over any mobile (speech_heard).

A **fight** of a SUBJECTS monster runs from its war flag going on (after IDLE_MIN_S idle or at first sighting; the flag
flaps between casts in a fight) to its death, its deletion or leaving view, Logan leaving the Mausoleum, its flag
staying off IDLE_MIN_S, or the capture's end. A fight first seen already in war mode has no flip (`flip` False).
Ours: Logan and the notoriety 2 tame/bonded mobiles (pets).

Its **spells** (casts): the effects whose source is it at another mobile (moving type 0 and the rest; effects on
itself are counted in `on_self`, not casts), plus a spell's fixed effect on one of ours (SPELL_FIXED, lightning type
1, Mind Blast's 0x374A on Logan paired with its own) within CAST_S after one of its CAST_ACTIONS 0x6E, within
FIXED_R of it, with no other monster within OTHER_R of that victim animating in that window and no moving effect from
another at it. Effects within GROUP_S are one cast (`targets`), each claiming its latest unclaimed cast 0x6E. A
cast's damage on a victim: the '-N' candidates over it within FLIGHT_S after the effect, ambiguous when another
monster within 1 tile of the victim animated within MELEE_S before the number or another monster's moving effect
reached it in between; `--summary` takes the unambiguous one nearest the kind's usual delay (the median over victims
with one candidate).
Its **melee**: a '-N' over one of ours within 1 tile of it, its SWING_ACTIONS 0x6E within MELEE_S before, no other
monster within 1 of the victim animating then. Its **steps**: each tile change with the nearest of ours before and
after it. Each 0x6E keeps the delay to the nearest '-N' over one of ours beside it (the `actions` table).

One row per fight in `<out>/<tag>.jsonl` after a {tag, capture, inside_s} row for a capture with Logan in the
Mausoleum (a capture that doesn't replay writes {tag, error}; one without Logan there nothing). `--summary` prints
the tables (JSON lines) from `<out>/*.jsonl`.
"""
import argparse
import bisect
import collections
import concurrent.futures
import glob
import json
import os
import re
import sys

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HARNESS)
sys.path.insert(0, HARNESS)

LOGAN = 0x00104A60
SUBJECTS = ("forgotten soul", "shadowguard", "vampire countess")
MONSTER_NOTORIETY = (3, 4, 5, 6)
WAR = 0x40
IDLE_MIN_S = 10.0     # a war flip after this long idle (or at first sighting) starts a fight; off this long ends it
CAST_S = 3.0          # a cast animation to its effect at most this long
GROUP_S = 0.1         # one cast's effects at several of ours within this
FLIGHT_S = 2.0        # an effect to its '-N' at most this long
MELEE_S = 0.15        # a swing's 0x6E to its '-N' at most this long
OTHER_R = 12          # another monster this close to the victim animating makes a fixed effect ambiguous
FIXED_R = 14          # a fixed effect on a victim farther than this from it isn't its (Outlands' view range is 18)
# fixed effects on the victim that are a Magery spell landing (ServUO's visuals [INFERENCE]): Flamestrike 0x3709,
# Explosion 0x36BD, Lightning / Chain Lightning (type 1); Mind Blast's 0x374A only on Logan and with the caster's own
# 0x374A at the same instant: the phoenix shows 0x374A on itself and on a monster at once on its own (77 times on
# itself in 20261031_105918 with none of SUBJECTS within 14; the countess's "pairs" on pets had a '-N' 7 times in 100),
# so on a pet the soul's Mind Blast can't be told from it. Not 0x5683 (the vampiric consort's bite, a crab's pinch)
# nor 0x376A (disease, heals).
SPELL_FIXED = frozenset((0x3709, 0x36BD))
MIND_BLAST = 0x374A
# Each type's 0x6E actions by role, from this scan's `actions` table (2026-10-31, 43 captures): a swing action's anims
# have a '-N' over one of ours beside it 0-0.05 s after (soul 16: 434 of 997; shadowguard 4: 124 of 304, 5: 110 of
# 275; countess 15: 188 of 546, 6: 142 of 282); the others have that 1-5 % of the time and are the ones before its
# effects (soul 233 before 45 of its 49 multi-target 0x36D4 volleys, 245 before its flamestrikes, 209 its lightning).
# The soul's 20 (1872 anims, a number 0-0.05 s after 40 times, spread -0.5..+1 s) is neither [INFERENCE: get-hit].
CAST_ACTIONS = {"forgotten soul": (209, 218, 230, 233, 245), "shadowguard": (6, 10), "vampire countess": (4, 16)}
SWING_ACTIONS = {"forgotten soul": (16,), "shadowguard": (4, 5), "vampire countess": (6, 15)}
MARKS = (3, 6, 10)    # seconds after the flip the distances are read at
NUMBER = re.compile(r"^-(\d+)$")


def _u16(p, o):
    return int.from_bytes(p[o:o + 2], "big")


def _u32(p, o):
    return int.from_bytes(p[o:o + 4], "big")


def _cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _at(series, t):
    """The last (t, x, y) of a time-sorted series at or before t, else None."""
    i = bisect.bisect_right(series, (t, float("inf"))) - 1
    return series[i] if i >= 0 else None


def collect(packets):
    """The raw record of a capture while Logan stands in the Mausoleum (module docstring)."""
    from sim.calibrate import in_region, type_key
    from world.runtime import WorldRuntime
    now = [None]
    rt = WorldRuntime(clock=lambda: now[0])
    st = rt.state
    pos = collections.defaultdict(list)       # serial -> [(t, x, y)]
    war = collections.defaultdict(list)       # serial -> [(t, war)] (first = first sighting)
    kind = {}                                 # serial -> type key (monsters) / "pet" / None
    gone = collections.defaultdict(list)      # serial -> [(t, why)]
    anims = collections.defaultdict(list)     # serial -> [(t, action)]
    shots = []                                # (t, type, src, tgt, graphic, x, y, tx, ty)
    numbers = collections.defaultdict(list)   # serial -> [(t, n)]
    inside = []                               # [(t, in region)] changes
    for t, d, _src, p in packets:
        now[0] = t
        rt.feed_packet(d, p)
        events = rt.drain_events()
        me = st.self
        here = me.serial == LOGAN and in_region(me.x, me.y, me.map)
        if not inside or inside[-1][1] != here:
            inside.append((t, here))
        if not here:
            continue
        if not pos[LOGAN] or pos[LOGAN][-1][1:] != (me.x, me.y):
            pos[LOGAN].append((t, me.x, me.y))
        for e in events:
            ev = e["ev"]
            if ev == "speech_heard" and (mm := NUMBER.match(e.get("text") or "")):
                numbers[e["serial"]].append((t, int(mm.group(1))))
            elif ev == "mobile_death":
                gone[e["serial"]].append((t, "dead"))
            elif ev == "prune":
                gone[e["serial"]].append((t, e["why"]))
            elif ev == "delete":
                gone[e["serial"]].append((t, "delete"))
        if d != "s2c" or not p:
            continue
        c = p[0]
        if c in (0x20, 0x77, 0x78, 0xD3):
            s = _u32(p, 3) if c in (0x78, 0xD3) else _u32(p, 1)
            m = st.mobiles.get(s)
            if m is None or s == LOGAN or m.x is None:
                continue
            if m.notoriety == 2 and m.pet in ("tame", "bonded"):
                kind[s] = "pet"
            elif m.notoriety in MONSTER_NOTORIETY and m.name:
                kind[s] = type_key(m.name)
            if not pos[s] or pos[s][-1][1:] != (m.x, m.y):
                pos[s].append((t, m.x, m.y))
            if c != 0x77:                     # Outlands' 0x77 carries no flags
                w = bool((m.flags or 0) & WAR)
                if not war[s] or war[s][-1][1] != w:
                    war[s].append((t, w))
        elif c == 0x6E and len(p) >= 14:
            anims[_u32(p, 1)].append((t, _u16(p, 5)))
        elif c == 0xC0 and len(p) >= 52:
            shots.append((t, p[1], _u32(p, 2), _u32(p, 6), _u32(p, 10), _u32(p, 14), _u32(p, 18), _u32(p, 26),
                          _u32(p, 30)))
        elif c == 0x70 and len(p) >= 28:
            shots.append((t, p[1], _u32(p, 2), _u32(p, 6), _u16(p, 10), _u16(p, 12), _u16(p, 14), _u16(p, 17),
                          _u16(p, 19)))
    end_t = now[0]
    return {"pos": pos, "war": war, "kind": kind, "gone": gone, "anims": anims, "shots": shots,
            "numbers": numbers, "inside": inside, "end": end_t,
            "gone_t": {s: [tg for tg, _why in g] for s, g in gone.items()}}


def _where(rec, s, t):
    """(x, y) of mobile s at t, None when unknown or deleted/dead/out of view since its last tile."""
    q = _at(rec["pos"].get(s, ()), t)
    if q is None:
        return None
    g = rec["gone_t"].get(s, ())
    i = bisect.bisect_left(g, q[0])
    if i < len(g) and g[i] <= t:
        return None
    return q[1], q[2]


def _inside_at(rec, t):
    q = _at(rec["inside"], t)
    return bool(q and q[1])


def spans(rec, s):
    """[(t0, t1, flip, why)] of monster s's fights (module docstring)."""
    war = rec["war"].get(s, [])
    seen = [q[0] for q in rec["pos"].get(s, ())] + [a[0] for a in rec["anims"].get(s, ())]
    seen.sort()
    dead = [tg for tg, why in rec["gone"].get(s, ()) if why == "dead"]
    left = [tg for tg, why in rec["gone"].get(s, ()) if why != "dead"]
    out = []
    i = 0
    while i < len(war):
        t, w = war[i]
        if not w:
            i += 1
            continue
        flip = i > 0 and (i == 1 or t - war[i - 1][0] >= IDLE_MIN_S)
        if i > 0 and not flip:
            i += 1                             # flapping inside an earlier fight's tail: not a wake
            continue
        t0 = t
        # its end: the first war-off held IDLE_MIN_S, death, a delete/prune with nothing of it within 5 s after,
        # Logan out of the Mausoleum, or the capture's end
        ends = [(rec["end"], "capture_end")]
        j = i + 1
        while j < len(war):
            if not war[j][1] and (j + 1 >= len(war) or war[j + 1][0] - war[j][0] >= IDLE_MIN_S):
                ends.append((war[j][0], "war_off"))
                break
            j += 1
        ends += [(tg, "dead") for tg in dead if tg >= t0][:1]
        for tg in left:
            if tg >= t0:
                k = bisect.bisect_right(seen, tg)
                if k >= len(seen) or seen[k] > tg + 5.0:
                    ends.append((tg, "out_of_view"))
                    break
        k = bisect.bisect_right(rec["inside"], (t0, True))
        for tq, here in rec["inside"][k:]:
            if not here:
                ends.append((tq, "logan_left"))
                break
        t1, why = min(ends)
        out.append((t0, t1, flip, why))
        i = j + 1 if j < len(war) else len(war)
        while i < len(war) and war[i][0] < t1:
            i += 1
    return out


def fights(rec, tag):
    """One row per SUBJECTS fight of the capture (module docstring)."""
    pets = sorted(s for s, k in rec["kind"].items() if k == "pet")
    ours = {LOGAN, *pets}
    monsters = {s for s, k in rec["kind"].items() if k not in (None, "pet")}
    all_anims = sorted((t, s) for s in monsters for t, _a in rec["anims"].get(s, ()))
    anim_ts = [a[0] for a in all_anims]
    shot_ts = [sh[0] for sh in rec["shots"]]

    def role(v):
        return "logan" if v == LOGAN else "pet"

    def others_animating(v, s, ta, tb, r):
        """Another monster than s within r of victim v animating in [ta, tb]."""
        for t, m in all_anims[bisect.bisect_left(anim_ts, ta):bisect.bisect_right(anim_ts, tb)]:
            if m == s:
                continue
            mp, vp = _where(rec, m, t), _where(rec, v, t)
            if mp is not None and vp is not None and _cheb(mp, vp) <= r:
                return True
        return False

    def others_shot(v, s, ta, tb):
        for sh in rec["shots"][bisect.bisect_left(shot_ts, ta):bisect.bisect_right(shot_ts, tb)]:
            if sh[1] == 0 and sh[3] == v and sh[2] != s and sh[2] in monsters:
                return True
        return False

    rows = []
    for s, k in rec["kind"].items():
        if k not in SUBJECTS:
            continue
        for t0, t1, flip, why in spans(rec, s):
            mine = [a for a in rec["anims"].get(s, ()) if t0 - CAST_S <= a[0] <= t1]

            def adj_number(s_, ta):
                """Seconds from its 0x6E at ta to the nearest '-N' (-0.5..+1.0 s) over one of ours beside it."""
                sp_ = _where(rec, s_, ta)
                best = None
                for v in ours:
                    vp_ = _where(rec, v, ta)
                    if sp_ is None or vp_ is None or _cheb(sp_, vp_) > 1:
                        continue
                    nums = rec["numbers"].get(v, [])
                    for tn, _n in nums[bisect.bisect_left(nums, (ta - 0.5,)):bisect.bisect_right(nums, (ta + 1.0,))]:
                        if best is None or abs(tn - ta) < abs(best):
                            best = tn - ta
                return None if best is None else round(best, 3)
            claimed = set()
            # its effects: moving at a mobile from it, fixed/lightning on one of ours after its 0x6E; on itself
            fx, on_self = [], collections.Counter()
            for sh in rec["shots"][bisect.bisect_left(shot_ts, t0 - 0.5):bisect.bisect_right(shot_ts, t1)]:
                t, typ, src, tgt, g = sh[:5]
                if src == s and tgt == s:
                    on_self[f"T{typ}:{g:#06x}"] += 1
                    continue
                if src == s and tgt and tgt != s:
                    fx.append((t, typ, g, tgt, "moving" if typ == 0 else "from_it"))
                elif typ in (1, 3) and src in ours and tgt in (src, 0) and src != s:
                    fx.append((t, typ, g, src, "fixed"))
            fx.sort()
            casts, i, dropped = [], 0, collections.Counter()
            while i < len(fx):
                grp = [fx[i]]
                while i + len(grp) < len(fx) and fx[i + len(grp)][0] - grp[0][0] <= GROUP_S:
                    grp.append(fx[i + len(grp)])
                i += len(grp)
                te = grp[0][0]
                prior = [a for a in mine if te - CAST_S <= a[0] <= te and a[0] not in claimed
                         and a[1] in CAST_ACTIONS[k]]
                anim = prior[-1] if prior else None
                sp = _where(rec, s, te)
                pair = any(x[2] == s and x[3] == s and x[1] == 3 and x[4] == MIND_BLAST
                           for x in rec["shots"][bisect.bisect_left(shot_ts, te - GROUP_S):
                                                 bisect.bisect_right(shot_ts, te + GROUP_S)])
                keep = []
                for g in grp:
                    if g[4] != "fixed":
                        keep.append(g)
                        continue
                    vp = _where(rec, g[3], te)
                    if not (g[1] == 1 or (g[1] == 3 and g[2] in SPELL_FIXED)
                            or (g[1] == 3 and g[2] == MIND_BLAST and pair and g[3] == LOGAN)):
                        dropped[f"T{g[1]}:{g[2]:#06x} not a spell"] += 1
                    elif anim is None:
                        dropped["no cast 0x6E of it before"] += 1
                    elif sp is None or vp is None or _cheb(sp, vp) > FIXED_R:
                        dropped[f"beyond {FIXED_R}"] += 1
                    elif others_animating(g[3], s, anim[0], te, OTHER_R) or others_shot(g[3], s, anim[0], te):
                        dropped["another monster cast/swung"] += 1
                    else:
                        keep.append(g)
                if not keep:
                    continue
                if anim is not None:
                    claimed.add(anim[0])
                targets = []
                for g in keep:
                    v = g[3]
                    vp = _where(rec, v, te)
                    cands = []
                    for tn, n in rec["numbers"].get(v, ()):
                        if te - 0.05 <= tn <= te + FLIGHT_S:
                            np_ = _where(rec, v, tn)
                            amb = others_shot(v, s, te, tn) or (np_ is not None and others_animating(
                                v, s, tn - MELEE_S, tn, 1))
                            cands.append([round(tn - te, 3), n, amb])
                    targets.append({"v": role(v) if v in ours else "other", "serial": v,
                                    "d": _cheb(sp, vp) if sp and vp else None, "cands": cands})
                kinds = sorted({f"T{g[1]}:{g[2]:#06x}" for g in keep})
                casts.append({"t": round(te - t0, 3), "anim_s": round(te - anim[0], 3) if anim else None,
                              "action": anim[1] if anim else None, "kind": "+".join(kinds),
                              "via": keep[0][4], "targets": targets})
            plain = [a for a in mine if a[0] not in claimed and a[0] >= t0 - 0.5 and a[1] in SWING_ACTIONS[k]]
            melee, first_swing = [], None
            for ta, act in plain:
                sp = _where(rec, s, ta)
                if sp is None:
                    continue
                adj = [v for v in ours if (vp := _where(rec, v, ta)) is not None and _cheb(sp, vp) <= 1]
                if adj and first_swing is None and ta >= t0:
                    first_swing = round(ta - t0, 3)
                for v in adj:
                    hit = next(((tn, n) for tn, n in rec["numbers"].get(v, ()) if ta <= tn <= ta + MELEE_S), None)
                    if hit is None or others_animating(v, s, hit[0] - MELEE_S, hit[0], 1):
                        continue
                    melee.append({"t": round(ta - t0, 3), "v": role(v), "n": hit[1], "action": act})
            # positions
            p0 = _where(rec, s, t0)
            lp = _where(rec, LOGAN, t0)
            pet_d = [_cheb(p0, q) for v in pets if p0 and (q := _where(rec, v, t0)) is not None]
            first = next((c for c in casts if c["targets"] and c["targets"][0]["v"] != "other"), None)
            fv = first["targets"][0]["serial"] if first else None
            marks = {}
            for m in (0, *MARKS):
                tm = t0 + m
                sp = _where(rec, s, tm)
                vp = _where(rec, fv, tm) if fv else None
                near = [(_cheb(sp, q)) for v in ours if sp and (q := _where(rec, v, tm)) is not None]
                marks[str(m)] = {"d_first_victim": _cheb(sp, vp) if tm <= t1 and sp and vp else None,
                                 "d_nearest_ours": min(near) if tm <= t1 and near else None,
                                 "moved": _cheb(sp, p0) if tm <= t1 and sp and p0 else None}
            track = [q for q in rec["pos"].get(s, ()) if t0 <= q[0] <= t1]
            # its own steps: the nearest of ours (where they stand then) before and after each tile change
            steps, prev = [], p0
            for q in track:
                if prev is not None and q[1:] != prev:
                    os_ = [o for v in ours if (o := _where(rec, v, q[0])) is not None]
                    if os_:
                        steps.append([round(q[0] - t0, 3), min(_cheb(prev, o) for o in os_),
                                      min(_cheb(q[1:], o) for o in os_)])
                prev = q[1:]
            rows.append({
                "tag": tag, "serial": s, "type": k, "flip": flip, "t0": round(t0, 3), "dur": round(t1 - t0, 3),
                "end": why, "pos0": p0, "d_logan0": _cheb(p0, lp) if p0 and lp else None,
                "d_pet0": min(pet_d) if pet_d else None,
                "first_victim": role(fv) if fv in ours else None,
                "max_moved": max((_cheb(q[1:], p0) for q in track), default=0) if p0 else None,
                "steps": steps, "marks": marks, "first_swing_s": first_swing,
                "casts": casts, "melee": melee, "on_self": dict(on_self), "dropped_fixed": dict(dropped),
                "anims": [[round(a[0] - t0, 3), a[1], a[0] in claimed, adj_number(s, a[0])]
                          for a in mine if a[0] >= t0 - 0.5]})
    return rows


def scan(job):
    import replay
    logs, tag = job
    try:
        rec = collect(replay.timed_packets(os.path.join(logs, f"session_{tag}")))
    except Exception as e:                       # noqa: BLE001 (a capture that doesn't replay is skipped, named)
        return tag, [{"tag": tag, "error": str(e)[:200]}]
    inside_s, ins = 0.0, rec["inside"] + [(rec["end"], False)]
    for (ta, h), (tb, _h) in zip(ins, ins[1:]):
        if h and ta is not None and tb is not None:
            inside_s += tb - ta
    if inside_s <= 0:
        return tag, []
    return tag, [{"tag": tag, "capture": True, "inside_s": round(inside_s, 1)}, *fights(rec, tag)]


def _q(values, ps=(0.1, 0.5, 0.9)):
    """Nearest-rank quantiles of numbers plus the max; None for none."""
    v = sorted(values)
    if not v:
        return None
    return [round(v[min(len(v) - 1, int(p * (len(v) - 1) + 0.5))], 2) for p in ps] + [round(v[-1], 2)]


def _pick(cands, d0):
    """The '-N' of a cast's victim: the unambiguous candidate nearest the kind's usual delay d0 (within 0.3 s), or
    the first one when d0 is unknown."""
    ok = [c for c in cands if not c[2]]
    if not ok:
        return None
    if d0 is None:
        return ok[0]
    best = min(ok, key=lambda c: abs(c[0] - d0))
    return best if abs(best[0] - d0) <= 0.3 else None


def summarize(rows):
    """The tables (dicts) of the module docstring's measures, per subject type."""
    caps = [r for r in rows if r.get("capture")]
    errs = [r for r in rows if "error" in r]
    fs = [r for r in rows if "type" in r]
    out = [{"table": "scanned", "captures_in_mausoleum": len(caps),
            "hours_in_mausoleum": round(sum(r["inside_s"] for r in caps) / 3600, 1), "errors": len(errs),
            "error_tags": [r["tag"] for r in errs],
            "fights": dict(collections.Counter(r["type"] for r in fs)),
            "fights_no_flip": dict(collections.Counter(r["type"] for r in fs if not r["flip"])),
            "ends": {k: dict(collections.Counter(r["end"] for r in fs if r["type"] == k)) for k in SUBJECTS}}]
    for k in SUBJECTS:
        rs = [r for r in fs if r["type"] == k]
        casts = [c for r in rs for c in r["casts"]]
        # the usual delay effect -> '-N' per kind, from victims with exactly one clean candidate
        d0 = {}
        for kind in {c["kind"] for c in casts}:
            ds = [t["cands"][0][0] for c in casts if c["kind"] == kind for t in c["targets"]
                  if len(t["cands"]) == 1 and not t["cands"][0][2]]
            d0[kind] = _q(ds)[1] if ds else None
        by = collections.defaultdict(list)
        for c in casts:
            label = c["kind"]
            label += " multi" if len([t for t in c["targets"] if t["v"] != "other"]) >= 2 else ""
            by[label].append(c)
        for label, cs in sorted(by.items(), key=lambda kv: -len(kv[1])):
            dmg = {"logan": [], "pet": []}
            dist, n_tg, actions, anim_s = [], [], collections.Counter(), []
            for c in cs:
                n_tg.append(len(c["targets"]))
                actions[c["action"]] += 1
                if c["anim_s"] is not None:
                    anim_s.append(c["anim_s"])
                for t in c["targets"]:
                    if t["d"] is not None:
                        dist.append(t["d"])
                    if t["v"] in dmg and (h := _pick(t["cands"], d0.get(c["kind"]))) is not None:
                        dmg[t["v"]].append(h[1])
            out.append({"table": "spells", "type": k, "kind": label, "casts": len(cs),
                        "via": dict(collections.Counter(c["via"] for c in cs)),
                        "targets_per_cast": dict(collections.Counter(n_tg)),
                        "victims": dict(collections.Counter(t["v"] for c in cs for t in c["targets"])),
                        "number_delay_s": d0.get(cs[0]["kind"]),
                        "n_dmg_logan": len(dmg["logan"]), "dmg_logan_q10_50_90_max": _q(dmg["logan"]),
                        "n_dmg_pet": len(dmg["pet"]), "dmg_pet_q10_50_90_max": _q(dmg["pet"]),
                        "dist_q10_50_90_max": _q(dist), "anim_to_effect_s": _q(anim_s),
                        "actions": dict(actions.most_common(6))})
        # reach: the distance from it to each of ours its casts reached, by how the cast was seen
        reach = collections.defaultdict(collections.Counter)
        for c in casts:
            for t in c["targets"]:
                if t["v"] != "other" and t["d"] is not None:
                    d, key = t["d"], f'{c["via"]}:{t["v"]}'
                    reach[key]["0-1" if d <= 1 else "2-4" if d <= 4 else "5-6" if d <= 6 else "7-9" if d <= 9
                               else "10-12" if d <= 12 else "13+"] += 1
                    reach[key]["max"] = max(reach[key]["max"], d)
        out.append({"table": "reach", "type": k, "by": {kk: dict(v) for kk, v in sorted(reach.items())}})
        mel = [m for r in rs for m in r["melee"]]
        out.append({"table": "melee", "type": k, "n": len(mel),
                    "n_logan": sum(1 for m in mel if m["v"] == "logan"),
                    "dmg_logan": _q([m["n"] for m in mel if m["v"] == "logan"]),
                    "n_pet": sum(1 for m in mel if m["v"] == "pet"),
                    "dmg_pet": _q([m["n"] for m in mel if m["v"] == "pet"]),
                    "actions": dict(collections.Counter(m["action"] for m in mel).most_common(6))})
        # cadence: gaps between its damaging casts on us, between its casts, between its cast-action animations
        gap_dmg, gap_anim, gap_cast = [], [], []
        for r in rs:
            ts = [c["t"] for c in r["casts"]
                  if any(t["v"] != "other" and _pick(t["cands"], d0.get(c["kind"])) for t in c["targets"])]
            gap_dmg += [b - a for a, b in zip(ts, ts[1:])]
            ts = [c["t"] for c in r["casts"]]
            gap_cast += [b - a for a, b in zip(ts, ts[1:])]
            ts = [a[0] for a in r["anims"] if a[1] in CAST_ACTIONS[k]]
            gap_anim += [b - a for a, b in zip(ts, ts[1:])]
        out.append({"table": "cadence", "type": k, "cast_actions": CAST_ACTIONS[k],
                    "n_gap_damaging_cast": len(gap_dmg), "gap_damaging_cast_s": _q(gap_dmg),
                    "n_gap_cast": len(gap_cast), "gap_cast_s": _q(gap_cast),
                    "n_gap_cast_anim": len(gap_anim), "gap_cast_anim_s": _q(gap_anim)})
        # its 0x6E actions: how many, how many claimed by a cast, and the delay to a '-N' over one of ours beside it
        acts = collections.defaultdict(collections.Counter)
        for r in rs:
            for a in r["anims"]:
                d = a[3]
                acts[a[1]]["n"] += 1
                acts[a[1]]["cast_claimed"] += a[2]
                acts[a[1]]["no_number_beside" if d is None else "num_before" if d < 0 else "num_0_0.05s"
                           if d <= 0.05 else "num_0.05_1s"] += 1
        out.append({"table": "actions", "type": k,
                    "by_action": {a: dict(c) for a, c in sorted(acts.items(), key=lambda kv: -kv[1]["n"])}})
        # its own steps: toward / level / away from the nearest of ours, while none of ours was beside it
        st = collections.Counter()
        toward10, fights_toward10 = [], 0
        flipped = [r for r in rs if r["flip"]]
        for r in rs:
            for _t, a, b in r["steps"]:
                if a > 1:
                    st["toward" if b < a else "away" if b > a else "level"] += 1
        for r in flipped:
            n = sum(1 for t, a, b in r["steps"] if t <= 10 and a > 1 and b < a)
            toward10.append(n)
            fights_toward10 += n > 0
        out.append({"table": "steps", "type": k, "steps_while_apart": dict(st),
                    "flip_fights": len(flipped), "fights_with_toward_step_in_10s": fights_toward10,
                    "toward_steps_in_10s": _q(toward10)})
        mv = {"table": "movement", "type": k, "fights": len(rs), "flip_fights": len(flipped)}
        for m in ("0", *map(str, MARKS)):
            for f in ("d_first_victim", "d_nearest_ours", "moved"):
                vals = [r["marks"][m][f] for r in flipped if r["marks"][m][f] is not None]
                mv[f"{f}@{m}s"] = {"n": len(vals), "q": _q(vals)}
        dists = [t["d"] for r in rs for c in r["casts"] for t in c["targets"][:1] if t["d"] is not None]
        mv["casts_from_5plus"] = f"{sum(1 for d in dists if d >= 5)}/{len(dists)}"
        sw = [r["first_swing_s"] for r in flipped if r["first_swing_s"] is not None]
        mv["first_swing_s"] = {"n": len(sw), "of": len(flipped), "q": _q(sw)}
        mh = [r["melee"][0]["t"] for r in flipped if r["melee"]]
        mv["first_melee_hit_s"] = {"n": len(mh), "of": len(flipped), "q": _q(mh)}
        mm = [r["max_moved"] for r in flipped if r["max_moved"] is not None]
        mv["max_moved_from_flip_tile"] = {"n": len(mm), "q": _q(mm)}
        dur = [r["dur"] for r in flipped]
        mv["fight_s"] = _q(dur)
        m10 = [r["marks"]["10"]["moved"] for r in flipped if r["marks"]["10"]["moved"] is not None]
        mv["still_at_10s"] = f"{sum(1 for m in m10 if m == 0)}/{len(m10)}"
        # after its first spell on us from 5+ tiles: did it step toward us (none of ours beside it) within 5 s?
        far, closed = 0, 0
        for r in flipped:
            c = next((c for c in r["casts"] if c["targets"] and c["targets"][0]["v"] != "other"), None)
            if c is None or c["targets"][0]["d"] is None or c["targets"][0]["d"] < 5:
                continue
            far += 1
            closed += any(c["t"] < t <= c["t"] + 5 and a > 1 and b < a for t, a, b in r["steps"])
        mv["stepped_toward_within_5s_of_first_spell_from_5plus"] = f"{closed}/{far}"
        # the fights it woke with none of ours within 4 (the runner's case: an idle one seen from range)
        fr = [r for r in flipped if (r["marks"]["0"]["d_nearest_ours"] or 0) >= 5]
        toward = sum(1 for r in fr if any(t <= 10 and a > 1 and b < a for t, a, b in r["steps"]))
        mv["far_start"] = {
            "fights": len(fr),
            "still_at_10s": f'{sum(1 for r in fr if r["marks"]["10"]["moved"] == 0)}'
                            f'/{sum(1 for r in fr if r["marks"]["10"]["moved"] is not None)}',
            "toward_step_while_apart_in_10s": f"{toward}/{len(fr)}",
            **{f"moved@{m}s": _q([r["marks"][str(m)]["moved"] for r in fr if r["marks"][str(m)]["moved"] is not None])
               for m in MARKS},
            **{f"d_nearest_ours@{m}s": _q([r["marks"][str(m)]["d_nearest_ours"] for r in fr
                                           if r["marks"][str(m)]["d_nearest_ours"] is not None]) for m in MARKS},
            "first_swing_s": _q([r["first_swing_s"] for r in fr if r["first_swing_s"] is not None]),
            "n_first_swing": sum(1 for r in fr if r["first_swing_s"] is not None)}
        out.append(mv)
        first = [r for r in flipped if r["first_victim"]]
        out.append({"table": "first_target", "type": k, "n": len(first),
                    "victim": dict(collections.Counter(r["first_victim"] for r in first)),
                    "d_logan_at_flip": _q([r["d_logan0"] for r in first if r["d_logan0"] is not None]),
                    "d_nearest_pet_at_flip": _q([r["d_pet0"] for r in first if r["d_pet0"] is not None]),
                    "logan_nearer_than_pets": sum(1 for r in first if r["d_logan0"] is not None
                                                  and r["d_pet0"] is not None and r["d_logan0"] < r["d_pet0"]),
                    "logan_first_when_nearer": sum(1 for r in first if r["first_victim"] == "logan"
                                                   and r["d_logan0"] is not None and r["d_pet0"] is not None
                                                   and r["d_logan0"] < r["d_pet0"]),
                    "no_spell": len(flipped) - len(first)})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--logs", default=os.path.join(REPO, "logs"))
    ap.add_argument("--since", default="20261010", help="captures from this day on (YYYYMMDD)")
    ap.add_argument("--out", default=os.path.join(REPO, "logs", "soul_scan"), help="directory for <tag>.jsonl")
    ap.add_argument("--summary", action="store_true", help="summarize <out>/*.jsonl instead of scanning")
    a = ap.parse_args(argv)
    if a.summary:
        rows = []
        for p in sorted(glob.glob(os.path.join(a.out, "*.jsonl"))):
            with open(p, encoding="utf-8") as f:
                rows.extend(json.loads(line) for line in f if line.strip())
        for o in summarize(rows):
            print(json.dumps(o))
        return 0
    import cpu_budget
    cpu_budget.limit()
    tags = sorted(os.path.basename(p)[len("session_"):-len(".s2c.raw")]
                  for p in glob.glob(os.path.join(a.logs, "session_*.s2c.raw"))
                  if os.path.basename(p)[8:16] >= a.since)
    os.makedirs(a.out, exist_ok=True)
    n = 0
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, cpu_budget.workers())) as ex:
        for tag, rows in ex.map(scan, [(a.logs, t) for t in tags]):
            n += any(r.get("capture") for r in rows)
            with open(os.path.join(a.out, f"{tag}.jsonl"), "w", encoding="utf-8") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
    print(f"{len(tags)} captures, {n} with Logan in the Mausoleum -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

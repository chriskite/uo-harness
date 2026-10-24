"""Cast and flight times of monster and pet ranged attacks in the live captures, for the simulator (docs/SIM.md).

    python harness/sim/cast_scan.py [--logs DIR] [--since YYYYMMDD] [--out DIR] [--summary]

Each capture is replayed through `WorldRuntime` in its row order; only packets while we stand in the Mausoleum
(sim.calibrate.in_region) count. The raw S2C 0x6E (animation) and 0xC0 (hued effect) are read here because the
runtime drops effects that don't involve us; the '-N' damage numbers come from its `speech_heard` events.

Two kinds of sample, one row each in `<out>/<tag>.jsonl` (a capture that does not replay writes one {tag, error} row):
- `effect`: a moving 0xC0 (type 0) from a monster or one of our pets to another mobile. `cast_s` is the time from the
  source's last 0x6E to the effect when that came within ANIM_WINDOW_S before it; `flight_s` the time from the effect
  to the first '-N' over the target within FLIGHT_WINDOW_S, dropped when another mobile within 1 tile of the target
  had a 0x6E within MELEE_S before that number (the melee attribution rule in docs/SIM.md).
- `anim_hit`: a monster 0x6E with no 0xC0 from it within ANIM_WINDOW_S after, then the first '-N' within
  ANIM_WINDOW_S on us or one of our pets 2+ tiles from it, with no other monster 0x6E within MELEE_S before that
  number: `anim_hit_s` (the types that cast without an effect).

A monster's key is sim.calibrate.type_key(name); our pet's (notoriety 2, tame/bonded) is `pet:<serial decimal>`.
`--summary` reads every `<out>/*.jsonl` and prints one JSON object per key (counts, `[q10, q90, q50]` of the times,
modes of the animation and effect fields) plus a `*monsters*` row pooling every non-pet effect sample.
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

ANIM_WINDOW_S = 3.0
FLIGHT_WINDOW_S = 2.0
MELEE_S = 0.15
MONSTER_NOTORIETY = (3, 4, 5, 6)
NUMBER = re.compile(r"^-(\d+)$")
EFFECT_FIELDS = ("graphic", "speed", "duration", "hue", "render", "explode")


def _u16(p, o):
    return int.from_bytes(p[o:o + 2], "big")


def _u32(p, o):
    return int.from_bytes(p[o:o + 4], "big")


def _cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def scan(job):
    import replay
    from sim.calibrate import in_region, type_key
    from world.runtime import WorldRuntime
    logs, tag = job
    try:
        packets = replay.timed_packets(os.path.join(logs, f"session_{tag}"))
    except Exception as e:                       # noqa: BLE001 (a capture that doesn't replay is skipped, named)
        return tag, [{"tag": tag, "error": str(e)[:200]}]
    now = [None]
    rt = WorldRuntime(clock=lambda: now[0])
    st = rt.state

    def info(serial):
        """(key, pos, ours, monster) of a mobile now; key None when unknown."""
        me = st.self
        if serial == me.serial:
            return "us", (me.x, me.y), True, False
        m = st.mobiles.get(serial)
        if m is None or m.x is None:
            return None, None, False, False
        if m.notoriety == 2 and m.pet in ("tame", "bonded"):
            return f"pet:{serial}", (m.x, m.y), True, False
        if m.notoriety in MONSTER_NOTORIETY and m.name:
            return type_key(m.name), (m.x, m.y), False, True
        return None, (m.x, m.y), False, False

    anims = collections.defaultdict(list)      # serial -> [(t, action, frames, repeat, delay, pos, monster)]
    all_anims = []                             # (t, serial, pos, monster)
    effects = []                               # (t, fields, src info, tgt pos)
    numbers = collections.defaultdict(list)    # serial -> [(t, pos, ours)]
    eff_from = collections.defaultdict(list)   # source serial -> [t] (any 0xC0 from it)
    try:
        for t, d, _src, p in packets:
            now[0] = t
            rt.feed_packet(d, p)
            me = st.self
            events = rt.drain_events()
            if not in_region(me.x, me.y, me.map):
                continue
            if d == "s2c" and p and p[0] == 0x6E and len(p) >= 14:
                s = _u32(p, 1)
                key, pos, _ours, monster = info(s)
                if pos is not None:
                    a = (t, _u16(p, 5), _u16(p, 7), _u16(p, 9), p[13], pos, monster, key)
                    anims[s].append(a)
                    all_anims.append((t, s, pos, monster))
            elif d == "s2c" and p and p[0] == 0xC0 and len(p) >= 52:
                src, tgt = _u32(p, 2), _u32(p, 6)
                eff_from[src].append(t)
                if p[1] == 0 and src and tgt and src != tgt:
                    key, pos, ours, monster = info(src)
                    if key is not None and (monster or key.startswith("pet:")):
                        f = {"graphic": _u32(p, 10), "x": _u32(p, 14), "y": _u32(p, 18), "tx": _u32(p, 26),
                             "ty": _u32(p, 30), "speed": p[38], "duration": p[39], "fixed_dir": p[42],
                             "explode": p[43], "hue": _u32(p, 44), "render": _u32(p, 48)}
                        effects.append((t, src, tgt, key, f))
            for e in events:
                if e["ev"] == "speech_heard" and NUMBER.match(e.get("text") or ""):
                    _k, pos, ours, _m = info(e["serial"])
                    if pos is not None:
                        numbers[e["serial"]].append((t, pos, ours))
    except Exception as e:                       # noqa: BLE001 (a capture that breaks mid-replay is skipped, named)
        return tag, [{"tag": tag, "error": str(e)[:200]}]

    all_anims.sort(key=lambda a: a[0])
    anim_ts = [a[0] for a in all_anims]

    def anims_before(tn, lo_s):
        i = bisect.bisect_right(anim_ts, tn)
        j = bisect.bisect_left(anim_ts, tn - lo_s)
        return all_anims[j:i]

    rows = []
    for t, src, tgt, key, f in effects:
        cast_s = flight_s = action = frames = repeat = delay = None
        prior = [a for a in anims.get(src, ()) if t - ANIM_WINDOW_S <= a[0] <= t]
        if prior:
            a = prior[-1]
            cast_s, action, frames, repeat, delay = round(t - a[0], 3), a[1], a[2], a[3], a[4]
        hit = next((n for n in numbers.get(tgt, ()) if t <= n[0] <= t + FLIGHT_WINDOW_S), None)
        if hit is not None:
            melee = any(s not in (src, tgt) and _cheb(pos, hit[1]) <= 1
                        for _ta, s, pos, _m in anims_before(hit[0], MELEE_S))
            if not melee:
                flight_s = round(hit[0] - t, 3)
        rows.append({"tag": tag, "t": round(t, 3), "src_key": key, "src": src, "tgt": tgt, "kind": "effect",
                     "action": action, "frames": frames, "repeat": repeat, "delay": delay, "cast_s": cast_s,
                     "flight_s": flight_s, "anim_hit_s": None, "graphic": f["graphic"], "speed": f["speed"],
                     "duration": f["duration"], "fixed_dir": f["fixed_dir"], "explode": f["explode"],
                     "hue": f["hue"], "render": f["render"],
                     "dist": max(abs(f["x"] - f["tx"]), abs(f["y"] - f["ty"]))})
    for s, lst in anims.items():
        for t, action, frames, repeat, delay, pos, monster, key in lst:
            if not monster:
                continue
            if any(t <= te <= t + ANIM_WINDOW_S for te in eff_from.get(s, ())):
                continue
            first = None
            for v, nums in numbers.items():
                for n in nums:
                    if t < n[0] <= t + ANIM_WINDOW_S and n[2] and v != s and _cheb(pos, n[1]) >= 2:
                        if first is None or n[0] < first[0]:
                            first = (n[0], v, n[1])
                        break
            if first is None:
                continue
            if any(s2 != s and m for _ta, s2, _p, m in anims_before(first[0], MELEE_S)):
                continue
            rows.append({"tag": tag, "t": round(t, 3), "src_key": key, "src": s, "tgt": first[1],
                         "kind": "anim_hit", "action": action, "frames": frames, "repeat": repeat, "delay": delay,
                         "cast_s": None, "flight_s": None, "anim_hit_s": round(first[0] - t, 3), "graphic": None,
                         "speed": None, "duration": None, "fixed_dir": None, "explode": None, "hue": None,
                         "render": None, "dist": _cheb(pos, first[2])})
    return tag, rows


def _q(values):
    """[q10, q90, q50] (nearest rank) of numbers, rounded to 0.01; None for none."""
    v = sorted(values)
    if not v:
        return None

    def q(p):
        return round(v[min(len(v) - 1, int(p * (len(v) - 1) + 0.5))], 2)
    return [q(0.1), q(0.9), q(0.5)]


def _mode(values):
    c = collections.Counter(v for v in values if v is not None)
    return c.most_common(1)[0][0] if c else None


def summarize(rows):
    """One dict per source key, plus the '*monsters*' pool of every non-pet effect sample."""
    by = collections.defaultdict(list)
    for r in rows:
        if "error" in r or r.get("src_key") is None:
            continue
        by[r["src_key"]].append(r)
        if r["kind"] == "effect" and not r["src_key"].startswith("pet:"):
            by["*monsters*"].append(r)
    out = []
    for key in sorted(by):
        rs = by[key]
        eff = [r for r in rs if r["kind"] == "effect"]
        cast = [r["cast_s"] for r in eff if r["cast_s"] is not None]
        flight = [r["flight_s"] for r in eff if r["flight_s"] is not None]
        ah = [r for r in rs if r["kind"] == "anim_hit"]
        o = {"src_key": key, "captures": len({r["tag"] for r in rs}), "n_effect": len(eff), "n_cast": len(cast),
             "n_flight": len(flight), "n_anim_hit": len(ah),
             "effect_action": _mode(r["action"] for r in eff),
             "anim_hit_action": _mode(r["action"] for r in ah),
             "cast_s": _q(cast), "flight_s": _q(flight), "anim_hit_s": _q(r["anim_hit_s"] for r in ah),
             "dist": _q(r["dist"] for r in eff)}
        o.update({k: _mode(r[k] for r in eff) for k in EFFECT_FIELDS})
        out.append(o)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--logs", default=os.path.join(REPO, "logs"))
    ap.add_argument("--since", default="20261010", help="captures from this day on (YYYYMMDD)")
    ap.add_argument("--out", default=os.path.join(REPO, "logs", "cast_scan"), help="directory for <tag>.jsonl")
    ap.add_argument("--summary", action="store_true", help="summarize <out>/*.jsonl instead of scanning")
    a = ap.parse_args(argv)
    if a.summary:
        rows = []
        for p in sorted(glob.glob(os.path.join(a.out, "*.jsonl"))):
            with open(p, encoding="utf-8") as f:
                rows.extend(json.loads(line) for line in f if line.strip())
        errors = sum(1 for r in rows if "error" in r)
        print(json.dumps({"captures": len({r["tag"] for r in rows}), "errors": errors, "rows": len(rows) - errors}))
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
            if rows:
                n += 1
            with open(os.path.join(a.out, f"{tag}.jsonl"), "w", encoding="utf-8") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
    print(f"{len(tags)} captures, {n} with samples -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

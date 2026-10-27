"""Logan's live barding against his effective barding skill: each creature type's difficulty for the simulator
(docs/SIM.md "Barding from the effective skill").

Inputs:
- the hunt task logs (`logs/tasks/hunt-*.log`): "Discordance on <type>: success (71 s)" / "failed", likewise
  Peacemaking and Provocation. The seconds are the server's own "(1m 11s)" (tamer.duration);
- the memory store's songs ("You are now under the effect of a Song of Discordance (17.7% ...)"): a song prints
  SONG_X x the effective barding skill E / 100, cut to a tenth (live 15.0 / 16.2 / 16.9 / 17.7 / 18.4 % at E 100 /
  108 / 113 / 118 / 123: 80 skill + the instrument's quality + the Mastery Chain's Effective Barding links). A bard
  line takes the E of the last song before it within SONG_MAX_AGE_S.

The model fitted (wiki Barding Difficulty, knowledge #8530/#5239/#11223): a success with chance
max((E + bonus - d) / 100, 0.33 x (E + bonus) / 100), at most 1; bonus 25 on a target already barded. A success's
duration tells whether it had the bonus (the duration rule below); a failure takes it when a Discordance succeeded
within BARDED_S before: on its type for Peacemaking, on any type for Provocation (the line names the creature incited;
the one it is incited onto is Logan's discorded target). Per skill and type with MIN_N answers: the d of maximum
likelihood. The durations of the same lines check the duration rule (scenario.bard_secs): ceil(base x (E + bonus) /
100), base 60 s for Discordance and 30 s for the others.

    python harness/sim/bard_scan.py [--logs DIR] [--db PATH] [--out PATH]
"""
from __future__ import annotations

import argparse
import bisect
import collections
import glob
import json
import math
import os
import re
import sqlite3
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))

SONG_X = 0.15                 # a song's share per 100 effective barding (the live prints above)
SONG_MAX_AGE_S = 3 * 3600
SONG_MIN = 12.0               # below: not Logan's (80 skill alone prints 12.0; 9.9 % once on 2026-10-23 21:26)
TEST_SHARD_TAGS = ("20261015_090607", "20261015_115604")   # docs/NOTES.md: the test shard's sessions (15.8 %)
BARDED_S = 75.0
BONUS = 25.0
MIN_N = 10
MIN_FACTOR = 0.33
BASE_S = {"discord": 60.0, "peace": 30.0, "provoke": 30.0}
KINDS = {"Discordance": "discord", "Peacemaking": "peace", "Provocation": "provoke"}
LINE = re.compile(r"^(\d\d):(\d\d):(\d\d) (Discordance|Peacemaking|Provocation) on (?:an? |the )?(.+?): "
                  r"(success|failed)(?: \((\d+) s\))?\s*$")
SONG = re.compile(r"under the effect of a Song of \w+ \(\+?([\d.]+)%")


def eff_of(song_pct: float) -> int:
    """The effective barding skill a song's print stands for: the least whole E printing it (prints are cut)."""
    return math.ceil(song_pct / SONG_X - 1e-6)


def chance(e: float, d: float) -> float:
    return min(1.0, max((e - d) / 100.0, MIN_FACTOR * e / 100.0))


def songs(db: str) -> list:
    c = sqlite3.connect(db)
    test = {r[0] for r in c.execute("select id from sessions where tag in (%s)" % ",".join("?" * len(TEST_SHARD_TAGS)),
                                    TEST_SHARD_TAGS)}
    out = []
    for sess, t, d in c.execute("select session, t, data from events where ev = 'speech_heard' "
                                "and data like '%under the effect of a Song of%' order by t"):
        m = SONG.search(d)
        if m and sess not in test and float(m.group(1)) >= SONG_MIN:
            out.append((t, float(m.group(1))))
    return out


def rows(logs: str, song_list: list) -> list:
    st = [s[0] for s in song_list]
    out = []
    for f in sorted(glob.glob(os.path.join(logs, "hunt-*.log"))):
        m = re.search(r"hunt-(\d{8})-(\d{6})", os.path.basename(f))
        day = time.mktime(time.strptime(m.group(1), "%Y%m%d"))
        start = time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S"))
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                g = LINE.match(line)
                if not g:
                    continue
                t = day + int(g[1]) * 3600 + int(g[2]) * 60 + int(g[3])
                if t < start - 60:
                    t += 86400                       # past midnight
                i = bisect.bisect_right(st, t) - 1
                if i < 0 or t - song_list[i][0] > SONG_MAX_AGE_S:
                    continue
                out.append({"t": t, "kind": KINDS[g[4]], "type": g[5].strip(), "ok": g[6] == "success",
                            "secs": int(g[7]) if g[7] else None, "song": song_list[i][1],
                            "eff": eff_of(song_list[i][1])})
    out.sort(key=lambda r: r["t"])
    last_ok, last_any = {}, -1e18
    for r in out:
        if r["secs"] is not None:            # a success: its duration shows the bonus
            r["bonus"] = BONUS if r["secs"] == math.ceil(BASE_S[r["kind"]] * (r["eff"] + BONUS) / 100 - 1e-9) else 0.0
        elif r["kind"] == "peace":
            r["bonus"] = BONUS if r["t"] - last_ok.get(r["type"], -1e18) <= BARDED_S else 0.0
        elif r["kind"] == "provoke":
            r["bonus"] = BONUS if r["t"] - last_any <= BARDED_S else 0.0
        else:
            r["bonus"] = 0.0
        if r["kind"] == "discord" and r["ok"]:
            last_ok[r["type"]] = last_any = r["t"]
    return out


def fit_d(obs: list) -> tuple:
    """The d (step 0.5, -100..300) of maximum likelihood for [(E, ok)]; with its log-likelihood."""
    best = (-math.inf, None)
    for d2 in range(-200, 601):
        d = d2 / 2
        ll = 0.0
        for e, ok in obs:
            p = min(max(chance(e, d), 1e-6), 1 - 1e-6)
            ll += math.log(p if ok else 1 - p)
        if ll > best[0]:
            best = (ll, d)
    return best[1], best[0]


def scan(logs: str, db: str) -> dict:
    sl = songs(db)
    rs = rows(logs, sl)
    kinds = {}
    for kind in BASE_S:
        by = collections.defaultdict(list)
        for r in rs:
            if r["kind"] == kind:
                by[r["type"]].append(r)
        types = {}
        for ty, obs in sorted(by.items()):
            if len(obs) < MIN_N:
                continue
            d, _ = fit_d([(r["eff"] + r["bonus"], r["ok"]) for r in obs])
            types[ty] = {"d": d, "ok": sum(r["ok"] for r in obs), "n": len(obs),
                         "eff": dict(sorted(collections.Counter(str(r["eff"]) for r in obs).items()))}
        kinds[kind] = {"types": types,
                       "median_d": statistics.median(v["d"] for v in types.values()) if types else None}
    durations = collections.defaultdict(collections.Counter)
    for r in rs:
        if r["secs"] is not None:
            durations[(r["kind"], r["eff"])][r["secs"]] += 1
    dur = []
    for (kind, e), cnt in sorted(durations.items()):
        plain = math.ceil(BASE_S[kind] * e / 100 - 1e-9)
        barded = math.ceil(BASE_S[kind] * (e + BONUS) / 100 - 1e-9)
        dur.append({"kind": kind, "eff": e, "seen": dict(cnt.most_common()), "rule_plain": plain,
                    "rule_barded": barded, "other": sum(n for s, n in cnt.items() if s not in (plain, barded))})
    eff_n = collections.Counter(r["eff"] for r in rs)
    return {"note": "generated by harness/sim/bard_scan.py (module docstring: the model and its evidence); "
                    "World._bard reads `kinds.<skill>.types.<type>.d`, else the skill's `median_d`",
            "generated": time.strftime("%Y-%m-%d %H:%M"), "lines": len(rs),
            "first": time.strftime("%Y-%m-%d %H:%M", time.localtime(rs[0]["t"])) if rs else None,
            "last": time.strftime("%Y-%m-%d %H:%M", time.localtime(rs[-1]["t"])) if rs else None,
            "eff_lines": {str(k): v for k, v in sorted(eff_n.items())},
            "min_n": MIN_N, "kinds": kinds, "durations": dur}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--logs", default=os.path.join(REPO, "logs", "tasks"))
    ap.add_argument("--db", default=os.path.join(REPO, "harness", "data", "harness.db"))
    ap.add_argument("--out", default=os.path.join(REPO, "harness", "data", "sim", "bard_observed.json"))
    a = ap.parse_args(argv)
    out = scan(a.logs, a.db)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    print(f"{out['lines']} answers {out['first']} .. {out['last']}; E: {out['eff_lines']}")
    for kind, k in out["kinds"].items():
        print(f"{kind}: {len(k['types'])} types with {MIN_N}+, median d {k['median_d']}")
    off = [d for d in out["durations"] if d["other"]]
    print(f"durations: {sum(sum(d['seen'].values()) for d in out['durations'])} seen, "
          f"{sum(d['other'] for d in off)} off the rule ({off})")
    print(f"-> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

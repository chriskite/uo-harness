"""Monster melee on Logan against his armor: does the wiki's armor rule hold on our captures?

    python harness/sim/armor_scan.py [--since 20261012] [--logs DIR] [--jobs 7] [--out PATH] [TAG ...]

Every capture of the character (`--character`, Logan Wolf) since `--since` is read once per scenario region (the
Mausoleum's, Tidal Tomb's: calibrate.configure) with calibrate.Session. Kept: each '-N' over us or a pet while exactly
one war-mode monster stood within a tile (calibrate's melee rule), with our status AR at that moment (0x11
`physical_resist`). Dropped: numbers on us during a bleed ("... bleed!" / "They begin to draw blood from you!" until
"You are no longer bleeding."), within tamer.POISON_S after '*looks violently ill*' over the victim, and within
DISEASE_S after "You have contracted a foul disease!" (ticks the armor doesn't touch, Armor & Weapons > Damage
Resolution, knowledge #11670).

Each kept number is song- and discord-corrected: divided by (1 - the Song of Discordance's share while it ran, from its
"You are now under the effect of a Song of Discordance (X% ..." line) and by (1 - bard.discord_dealt) when the attacker
was discorded (a successful Discordance on it within the use's duration).

The prediction (sim/armor.py, wiki Armor Values #11677): wiki melee U(lo, hi) x (1 - U(AR/300, AR/150)). Printed per
creature type and AR: n, the corrected numbers' p10/median/p90 and the prediction's; then all types pooled as
corrected / wiki mid by AR against the rule's mean 1 - AR/200, and the pets' numbers as corrected / wiki mid.
"""

import argparse
import collections
import json
import multiprocessing
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.dirname(HERE)
REPO = os.path.dirname(HARNESS)
for p in (HARNESS, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import armor  # noqa: E402
import calibrate  # noqa: E402
import tamer  # noqa: E402

SCENARIOS = ("mausoleum", "tidal_tomb")
SONG = re.compile(r"Song of Discordance \((\d+(?:\.\d+)?)% reduced damage")
BLEED_START = ("causing you to bleed", "They begin to draw blood from you!")
BLEED_END = "You are no longer bleeding."
DISEASE = "You have contracted a foul disease!"
DISEASE_S = 25.0


def _kb():
    out = {}
    for sc in SCENARIOS:
        with open(os.path.join(calibrate.SCENARIO_DIR, f"{sc}_kb.json"), encoding="utf-8") as f:
            for k, c in json.load(f)["creatures"].items():
                if c.get("melee_damage"):
                    out.setdefault(k, c["melee_damage"])
    return out


def _bard(sc):
    with open(os.path.join(calibrate.SCENARIO_DIR, "mausoleum.json" if sc == "tidal_tomb" else f"{sc}.json"),
              encoding="utf-8") as f:
        return json.load(f)["bard"]


def _windows(sess):
    """(songs [(t0, t1, share)], bleeds [(t0, t1)], diseases [t]) on us from the system lines."""
    songs, bleeds, diseases, open_bleed = [], [], [], None
    for t, x in sess.system:
        m = SONG.search(x)
        if m:
            songs.append([t, t + 900.0, float(m.group(1)) / 100])
        elif tamer.SONG_ENDS.match(x) and "Discordance" in x and songs and songs[-1][1] > t:
            songs[-1][1] = t
        elif any(s in x for s in BLEED_START):
            open_bleed = open_bleed if open_bleed is not None else t
        elif x == BLEED_END and open_bleed is not None:
            bleeds.append((open_bleed, t))
            open_bleed = None
        elif x == DISEASE:
            diseases.append(t)
    if open_bleed is not None:
        bleeds.append((open_bleed, float("inf")))
    return songs, bleeds, diseases


def scan(job):
    tag, sc, logs, who = job
    calibrate.configure(sc)
    try:
        sess = calibrate.Session(tag, logs).run()
    except Exception as e:   # a capture the replay can't read
        return tag, sc, {"error": repr(e)[:120]}
    if sess.t0 is None or sess.self_name != who:
        return tag, sc, None
    bard = _bard(sc)
    songs, bleeds, diseases = _windows(sess)
    discorded = collections.defaultdict(list)
    for use in sess.bard:
        if use["skill"] == tamer.SKILL_DISCORD and use["outcome"] == "ok" and use["targets"]:
            dur = use.get("duration_s") or bard["seconds"]["discord"]
            discorded[use["targets"][-1]["serial"]].append((use["t"], use["t"] + dur))
    ill = collections.defaultdict(list)
    for t, s, x in sess.overhead:
        if x == tamer.TEXT_POISONED:
            ill[s].append(t)
    rows = []
    for h in sess.victim_hits:
        if len(h["melee"]) != 1:
            continue
        t, v, a = h["t"], h["victim"], h["melee"][0]
        us = v == sess.self_serial
        if calibrate.nearest_within(ill[v], t, tamer.POISON_S, after=False):
            continue
        if us and (any(b0 <= t <= b1 for b0, b1 in bleeds) or calibrate.nearest_within(diseases, t, DISEASE_S, after=False)):
            continue
        song = next((x for s0, s1, x in songs if s0 <= t <= s1), 0.0)
        disc = any(d0 <= t <= d1 for d0, d1 in discorded.get(a, ()))
        rows.append({"tag": tag, "scenario": sc, "t": round(t - sess.t0, 2), "victim": "us" if us else sess.pets.get(v),
                     "type": calibrate.creature_type(sess, a), "amount": h["amount"], "ar": h["ar"],
                     "song": song, "discorded": disc,
                     "corrected": h["amount"] / ((1 - song) * (1 - (bard["discord_dealt"] if disc else 0.0)))})
    return tag, sc, {"rows": rows, "armor_s": {str(k): round(x, 1) for k, x in sorted(sess.armor.items())}}


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * (len(v) - 1) + 0.5))]


def summary(rows, kb):
    us = [r for r in rows if r["victim"] == "us" and r["ar"]]
    print("== per type and AR: corrected numbers on us (p10 / median / p90) vs the rule's prediction ==")
    by = collections.defaultdict(list)
    for r in us:
        by[(r["type"], r["ar"])].append(r["corrected"])
    for (k, ar), v in sorted(by.items()):
        w = kb.get(k)
        pred = armor.predict(w, ar) if w else None
        ps = f"{pred['p10']:.0f} / {pred['median']:.0f} / {pred['p90']:.0f}" if pred else "no wiki melee"
        print(f"  {k:28s} AR {ar:3d} n={len(v):3d}  live {q(v, .1):5.1f} / {statistics.median(v):5.1f} / "
              f"{q(v, .9):5.1f}   rule {ps}   wiki {w}")
    print("== pooled: corrected / wiki mid, by AR (rule mean 1 - AR/200) ==")
    pool = collections.defaultdict(list)
    for r in us:
        w = kb.get(r["type"])
        if w:
            pool[r["ar"]].append(r["corrected"] / ((w[0] + w[1]) / 2))
    for ar, v in sorted(pool.items()):
        print(f"  AR {ar:3d} n={len(v):4d}  median {statistics.median(v):.3f}  mean {statistics.fmean(v):.3f}  "
              f"p10 {q(v, .1):.3f}  p90 {q(v, .9):.3f}   rule mean {armor.mean_taken(ar):.3f}")
    print("== pets: corrected / wiki mid ==")
    pets = collections.defaultdict(list)
    for r in rows:
        w = kb.get(r["type"])
        if r["victim"] != "us" and w:
            pets[(r["victim"], r["tag"][:8])].append(r["corrected"] / ((w[0] + w[1]) / 2))
    for (p, day), v in sorted(pets.items()):
        print(f"  {str(p):16s} {day}  n={len(v):4d}  median {statistics.median(v):.3f}  mean {statistics.fmean(v):.3f}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tags", nargs="*")
    ap.add_argument("--since", default="20261012")
    ap.add_argument("--logs", default=calibrate.DEFAULT_LOGS)
    ap.add_argument("--character", default="Logan Wolf")
    ap.add_argument("--jobs", type=int, default=7)
    ap.add_argument("--out", help="write every kept row as JSON")
    a = ap.parse_args(argv)
    tags = a.tags or sorted({os.path.basename(p)[len("session_"):-len(".s2c.raw")]
                             for p in calibrate.glob.glob(os.path.join(a.logs, "session_*.s2c.raw"))
                             if os.path.basename(p)[len("session_"):] >= a.since})
    jobs = [(t, sc, a.logs, a.character) for t in tags for sc in SCENARIOS]
    rows, armor_s = [], {}
    with multiprocessing.Pool(a.jobs) as pool:
        for tag, sc, r in pool.imap_unordered(scan, jobs):
            if r is None:
                continue
            if "error" in r:
                print(f"{tag} {sc}: {r['error']}", file=sys.stderr)
                continue
            rows.extend(r["rows"])
            armor_s[f"{tag} {sc}"] = r["armor_s"]
    rows.sort(key=lambda r: (r["tag"], r["scenario"], r["t"]))
    print("== captures: seconds in the region per AR ==")
    for k, v in sorted(armor_s.items()):
        print(f"  {k}: {v}")
    summary(rows, _kb())
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({"rows": rows, "armor_s": armor_s}, f, indent=0)


if __name__ == "__main__":
    main()

"""Where Logan Wolf's live Mausoleum stealthers (aggro.stealthy: the vampiric consort, shade wolf, nightstalker) come out
of hiding and bite, against their known spawn spots (the runner's backspawn: harness/data/sim/mausoleum.json spawns).

    python harness/sim/stealth_scan.py [--logs DIR] [--since YYYYMMDD] [--out DIR] [--summary]

Each capture is replayed as sim/soul_scan.py does (`collect`: every mobile's tile, war flags, deaths and deletes,
the '-N' numbers over any mobile) while Logan stands in the Mausoleum. An **emergence** of a stealther: a tile for its
serial when it was out of view (its first sighting, or after a delete: Vanish and hiding delete it from our view).
Its **bite**: the first '-N' over one of ours (Logan or a pet, notoriety 2 tame/bonded) within 1 tile of it within
BITE_S after the emergence. One row per emergence in `<out>/<tag>.jsonl`: its type, time, tile, whether it is the
serial's first sighting, war mode, the nearest of ours then, the nearest known spot of its type (`spot`, `d_spot`),
the bite (victim, number, the victim's tile and its distance from the spot, Logan's distance from the spot and from
the nearest pet then) and the victim's numbers in the BLEED_S after the bite (`after`: [s after it, number]; the
consort's bleed).

`--summary` prints per type: emergences, the first sightings that bit at once ("from hiding": the serial's first
tile with one of ours within 1), the bites' distances (the stealther's tile and its victim's from the spot) as
quantiles, Logan's bites with the pets' distance, the numbers on him, the numbers on a victim 3 s after a bite (a
bleed tick's place) and, of the bites by one coming out within HOME_R of its spot, how far the victim stood from it
(`home_bite_victim_d_spot`).
"""
import argparse
import bisect
import collections
import concurrent.futures
import glob
import json
import os
import sys

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HARNESS)
sys.path.insert(0, HARNESS)

from sim import soul_scan  # noqa: E402

LOGAN = soul_scan.LOGAN
BITE_S = 1.0          # an emergence to its bite at most this long (live 11:57:17.30 consort tile, -46 the same instant)
BLEED_S = 16.0        # the victim's numbers after a bite kept this long (the consort's -46 then the bleed's -29 3 s later)
TICK_S = (2.85, 3.15)  # a number on its victim this long after a bite: a bleed tick's place (sim/world.py, 3 s apart)
SCENARIO = os.path.join(HARNESS, "data", "sim", "mausoleum.json")
HOME_R = 3            # a bite whose stealther came out this near its spot: from (or by) its spot ("home" bites)


def spots():
    """{type: [(x, y)]} of the stealthers' known spawn spots (the runner's seed_backspawn source)."""
    import aggro
    with open(SCENARIO, encoding="utf-8") as f:
        sc = json.load(f)
    out = {}
    for s in sc.get("spawns") or []:
        if aggro.stealthy(s["key"]):
            out.setdefault(s["key"], []).append(tuple(s["at"][:2]))
    return out


def emergences(rec, tag, known):
    import aggro
    pets = {s for s, k in rec["kind"].items() if k == "pet"}
    rows = []
    for s, kind in rec["kind"].items():
        if kind in (None, "pet") or not aggro.stealthy(kind):
            continue
        gone = rec["gone_t"].get(s, [])
        series = rec["pos"].get(s, [])
        last_t = None
        for i, (t, x, y) in enumerate(series):
            # out of view since its last tile: a delete/prune between the last tile and this one, or the first tile
            k = bisect.bisect_right(gone, last_t) if last_t is not None else 0
            fresh = last_t is None or (k < len(gone) and gone[k] <= t)
            last_t = t
            if not fresh or not soul_scan._inside_at(rec, t):
                continue
            ours = [(o, soul_scan._where(rec, o, t)) for o in [LOGAN, *pets]]
            ours = [(o, w) for o, w in ours if w is not None]
            near = min((soul_scan._cheb((x, y), w) for _, w in ours), default=None)
            sp = min(known.get(kind, []), key=lambda q: soul_scan._cheb(q, (x, y)), default=None)
            q = soul_scan._at(rec["war"].get(s, []), t + 0.05)
            row = {"tag": tag, "serial": s, "kind": kind, "t": round(t, 2), "at": [x, y], "first": i == 0,
                   "war": bool(q and q[1]), "near": near,
                   "spot": list(sp) if sp else None, "d_spot": soul_scan._cheb(sp, (x, y)) if sp else None}
            nxt = gone[k] if k < len(gone) and gone[k] > t else t + BITE_S
            bite = None
            for o, _w in ours:
                for tn, n in rec["numbers"].get(o, ()):
                    if t - 0.05 <= tn <= min(t + BITE_S, nxt + 0.05):
                        wo = soul_scan._where(rec, o, tn)
                        here = soul_scan._where(rec, s, tn) or (x, y)
                        if wo is not None and soul_scan._cheb(wo, here) <= 1:
                            if bite is None or tn < bite[0]:
                                bite = (tn, o, n, wo)
                            break
            if bite is not None:
                tn, o, n, wo = bite
                lw = soul_scan._where(rec, LOGAN, tn)
                pd = [soul_scan._cheb(lw, w) for p in pets if (w := soul_scan._where(rec, p, tn)) is not None] \
                    if lw is not None else []
                row["bite"] = {"dt": round(tn - t, 2), "on": "Logan" if o == LOGAN else "pet", "n": n, "victim_at": list(wo),
                               "victim_d_spot": soul_scan._cheb(sp, wo) if sp else None,
                               "logan_d_spot": soul_scan._cheb(sp, lw) if sp and lw else None,
                               "logan_pet_d": min(pd) if pd else None,
                               "after": [[round(tn2 - tn, 2), n2] for tn2, n2 in rec["numbers"].get(o, ())
                                         if tn < tn2 <= tn + BLEED_S]}
            rows.append(row)
    return rows


def scan(job):
    import replay
    logs, tag = job
    try:
        rec = soul_scan.collect(replay.timed_packets(os.path.join(logs, f"session_{tag}")))
    except Exception as e:                       # noqa: BLE001 (a capture that doesn't replay is skipped, named)
        return tag, [{"tag": tag, "error": str(e)[:200]}]
    if not any(here for _t, here in rec["inside"]):
        return tag, []
    return tag, [{"tag": tag, "capture": True}, *emergences(rec, tag, spots())]


def summarize(rows):
    out = []
    by = {}
    for r in rows:
        if r.get("kind"):
            by.setdefault(r["kind"], []).append(r)
    for kind, rs in sorted(by.items()):
        bites = [r for r in rs if r.get("bite")]
        hiding = [r for r in bites if r["first"]]
        on_logan = [r for r in bites if r["bite"]["on"] == "Logan"]
        out.append({
            "kind": kind, "emergences": len(rs), "first_sightings": sum(r["first"] for r in rs), "bites": len(bites),
            "bites_at_first_sighting": len(hiding),
            "first_bite_d_spot": soul_scan._q([r["d_spot"] for r in hiding if r["d_spot"] is not None]),
            "first_bite_victim_d_spot": soul_scan._q([r["bite"]["victim_d_spot"] for r in hiding
                                                      if r["bite"]["victim_d_spot"] is not None]),
            "first_bites_within": {d: sum(1 for r in hiding if r["bite"]["victim_d_spot"] is not None
                                          and r["bite"]["victim_d_spot"] <= d) for d in (1, 2, 3, 4, 5, 6, 8)},
            "bite_victim_d_spot": soul_scan._q([r["bite"]["victim_d_spot"] for r in bites
                                                if r["bite"]["victim_d_spot"] is not None]),
            "bites_on_logan": len(on_logan),
            "on_logan": [{"tag": r["tag"], "t": r["t"], "first": r["first"], "at": r["at"], "spot": r["spot"],
                          "logan_d_spot": r["bite"]["logan_d_spot"], "pet_d": r["bite"]["logan_pet_d"],
                          "n": r["bite"]["n"], "after": r["bite"]["after"]} for r in on_logan],
            "bite_numbers_logan": soul_scan._q([r["bite"]["n"] for r in on_logan]),
            "bite_numbers_pets": soul_scan._q([r["bite"]["n"] for r in bites if r["bite"]["on"] == "pet"]),
            "with_a_number_3s_after": sum(any(TICK_S[0] <= a[0] <= TICK_S[1] for a in r["bite"]["after"]) for r in bites),
            "with_a_number_1_5s_after": sum(any(1.35 <= a[0] <= 1.65 for a in r["bite"]["after"]) for r in bites),
            "numbers_3s_after": soul_scan._q([a[1] for r in bites for a in r["bite"]["after"]
                                              if TICK_S[0] <= a[0] <= TICK_S[1]])})
        home = [r for r in bites if r["d_spot"] is not None and r["d_spot"] <= HOME_R]
        out[-1]["home_bites"] = len(home)
        out[-1]["home_bite_victim_d_spot"] = dict(sorted(collections.Counter(
            r["bite"]["victim_d_spot"] for r in home).items()))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--logs", default=os.path.join(REPO, "logs"))
    ap.add_argument("--since", default="20261010", help="captures from this day on (YYYYMMDD)")
    ap.add_argument("--out", default=os.path.join(REPO, "logs", "stealth_scan"), help="directory for <tag>.jsonl")
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

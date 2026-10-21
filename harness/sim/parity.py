"""Live against simulated: the same measures from Logan Wolf's live pets runs and from a simulator batch, per hour on
the hunting level, with the live 95 % interval (docs/SIM.md "Parity with the live runs").

    python harness/sim/run.py --runs 144 --seed 17 --others --eerie --prior <main checkout>/harness/data/harness.db \
        --json -- --vet-range 5 --crawl-learn-s 180 > sim.jsonl
    python harness/sim/parity.py sim.jsonl [--db harness/data/harness.db] [--logs logs] [--since 2026-10-15T21:10]

Both sides are the runner's own records: the visits (`episodes`, loop hunt, style pets), its `fight`, `death` and
`leave` job events. Live they come from the memory store (the main checkout's), simulated from run.py's `--json`
summaries (`visits`, `fights`, `death_events`, `leave_events`). What the runner doesn't record comes live from the
captures (every overhead "-N" on the pets in the visits' windows, Logan's put on the nearest monster in war mode
within 12 tiles, each monster's first sighting on the level; cached in <logs>/parity_live_scan.json) and simulated
from the world's own accounting (`pet_damage_taken`, `player_damage_by`, `met`); the eerie corpses live from the
proxy's `eerie_corpse` events. The interval is a bootstrap over the live visits (each visit's counts and hours
resampled together); a simulated rate outside it is marked `<<` or `>>`. Deaths in a visit left for a red or a
hostile player don't count on either side (the simulator has no such players).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import re
import sqlite3
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.dirname(HERE)
ROOT = os.path.dirname(HARNESS)
sys.path.insert(0, HARNESS)

# the first pets run on the live server (docs/NOTES.md "The first run on the live server"); before it the test shard
LIVE_SINCE = "2026-10-15T21:10"
EERIE_SINCE = datetime.datetime(2026, 10, 17).timestamp()   # the Field of Souls event began (docs/NOTES.md)
BOOT = 2000
VIEW = 18          # the client's view range (combat.VIEW_RANGE): a corpse this far off a spot's box was seen from it
# the visit's counters compared, per hour on the level
COUNTS = ["kills", "gold", "hits_lost", "potions", "vet", "standoffs", "retreats", "pull_holds", "soul_jars",
          "lost_kills", "discord_ok", "discord_fail", "calm_ok", "calm_fail"]
NUM = re.compile(r"^([-+])(\d+)$")


def type_key(name: str | None) -> str:
    n = (name or "?").strip()
    for art in ("a ", "an ", "the "):
        if n.lower().startswith(art):
            return n[len(art):]
    return n


# ---------------------------------------------------------------- live
def live_visits(db: str, since: float, serial: int, box: list) -> tuple[list[dict], list[dict]]:
    """The live pets visits since `since` (with their deaths and leaves, and `_eerie`: the eerie corpses the proxy
    first saw in them within `box`, its `eerie_corpse` events) and the fights in them."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    visits = [json.loads(d) for (d,) in con.execute(
        "SELECT data FROM episodes WHERE loop='hunt' AND t_start>=? AND (char_serial=? OR char_serial IS NULL) "
        "ORDER BY t_start", (since, serial))]
    visits = [v for v in visits if v.get("style") == "pets" and v.get("t_end")]
    ev = [(k, t, x, y, json.loads(d)) for k, t, x, y, d in con.execute(
        "SELECT kind, t, x, y, data FROM job_events WHERE job='hunt' AND kind IN ('fight', 'death', 'leave') "
        "AND t>=? AND (char_serial=? OR char_serial IS NULL) ORDER BY t", (since, serial))]
    eerie = [(t, json.loads(d)) for t, d in con.execute("SELECT t, data FROM events WHERE ev='eerie_corpse' AND t>=?",
                                                       (since,))]
    con.close()
    for v in visits:
        v["_eerie"] = sum(1 for t, e in eerie if v["t_start"] <= t <= v["t_end"]
                          and box[0] <= e.get("x", -1) <= box[2] and box[1] <= e.get("y", -1) <= box[3])
    for v in visits:
        v["_deaths"], v["_leaves"] = [], []
    fights = []
    for k, t, x, y, d in ev:
        # a record belongs to the last visit started before it (a death on the walk out comes after the visit's end)
        v = next((v for v in reversed(visits) if v["t_start"] <= t), None)
        if v is None:
            continue
        if k == "fight":
            if t <= v["t_end"]:
                fights.append(d)
        elif k == "death" and t <= v["t_end"] + 600:
            v["_deaths"].append({"x": x, "y": y, "cause": d.get("cause")})
        elif k == "leave" and t <= v["t_end"] + 60:
            v["_leaves"].append({"why": d.get("why"), "died": "died" in str(d.get("gate"))})
    return visits, fights


def capture_windows(logs: str, visits: list[dict]) -> dict[str, list[list[float]]]:
    """{capture base: [[t_start, t_end], ...]} for the visits (the capture whose rows span each visit's start)."""
    import glob
    spans = {}
    for f in sorted(glob.glob(os.path.join(logs, "session_*.jsonl"))):
        with open(f, encoding="utf-8") as fh:
            first = next((json.loads(line).get("t") for line in fh if '"t"' in line), None)
        with open(f, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 20000))
            tail = fh.read().decode("utf-8", "ignore").splitlines()[1:]
        last = None
        for line in reversed(tail):
            try:
                last = json.loads(line).get("t")
            except json.JSONDecodeError:
                continue
            if last:
                break
        if first and last:
            spans[f[:-len(".jsonl")]] = (first, last)
    out = {}
    for v in visits:
        cap = next((c for c, (a, b) in spans.items() if a <= v["t_start"] <= b), None)
        if cap:
            out.setdefault(cap, []).append([v["t_start"], v["t_end"]])
    return out


SCAN_V = 3         # the capture scan's version: a cached window of another is scanned again
HUMAN_BODIES = (400, 401, 605, 606)


def _scan(job) -> list[dict]:
    """One capture replayed (replay.timed_packets through WorldRuntime): per window, the overhead "-N" on each pet,
    Logan's by the nearest monster in war mode within 12 tiles, and the monsters met on the level (each serial's first
    sighting with us in `area`, by name; a hostile copy of a pet counts as a transfixed copy; a human body only by a
    creature type of the scenario's, `kinds`: the souls and knights are human-bodied, players too)."""
    import replay
    from world.runtime import WorldRuntime
    cap, wins, me_serial, pets, area, kinds = job
    now = [None]
    rt = WorldRuntime(clock=lambda: now[0])
    out = [{"v": SCAN_V, "t0": a, "t1": b, "pets": {n: 0 for n in pets.values()}, "us_by": {}, "met": {}}
           for a, b in wins]
    met = set()
    for t, d, _src, p in replay.timed_packets(cap):
        now[0] = t
        rt.feed_packet(d, p)
        evs = rt.drain_events()
        w = next((o for o in out if o["t0"] <= t <= o["t1"]), None)
        if w is None:
            continue
        me = rt.state.self
        if me.x is not None and area[0] <= me.x <= area[2] and area[1] <= me.y <= area[3]:
            for ms, mm in rt.state.mobiles.items():
                if ms in met or mm.x is None or ms in pets or mm.notoriety not in (3, 4, 5, 6):
                    continue
                name = mm.name or rt.state.labels.get(ms)
                if name is None:
                    continue                  # its name comes a packet later
                met.add(ms)
                k = "transfixed copy" if name in pets.values() else type_key(name)
                if mm.graphic in HUMAN_BODIES and k not in kinds:
                    continue                  # a player
                w["met"][k] = w["met"].get(k, 0) + 1
        for e in evs:
            if e["ev"] != "speech_heard" or not (m := NUM.match(e.get("text") or "")) or m.group(1) != "-":
                continue
            s, n = e["serial"], int(m.group(2))
            if s in pets:
                w["pets"][pets[s]] += n
            elif s == me_serial:
                me, best = rt.state.self, None
                for ms, mm in rt.state.mobiles.items():
                    if mm.x is None or ms == me_serial or ms in pets or mm.notoriety in (1, 2) \
                            or not (mm.flags or 0) & 0x40:
                        continue
                    dd = max(abs(mm.x - me.x), abs(mm.y - me.y))
                    if dd <= 12 and (best is None or dd < best[0]):
                        best = (dd, type_key(mm.name))
                k = best[1] if best else "unknown"
                w["us_by"][k] = w["us_by"].get(k, 0) + n
    return out


def live_scan(logs: str, visits: list[dict], me_serial: int, pets: dict[int, str], area: list,
              kinds: set) -> list[dict]:
    """The capture scan for every visit, cached in <logs>/parity_live_scan.json by window."""
    cache_path = os.path.join(logs, "parity_live_scan.json")
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    cache = {k: r for k, r in cache.items() if r.get("v") == SCAN_V}
    jobs = capture_windows(logs, visits)
    todo = {c: [w for w in ws if f"{w[0]:.1f}" not in cache] for c, ws in jobs.items()}
    todo = {c: ws for c, ws in todo.items() if ws}
    if todo:
        from concurrent.futures import ProcessPoolExecutor
        import cpu_budget
        cpu_budget.limit()
        with ProcessPoolExecutor(max_workers=max(1, min(len(todo), cpu_budget.workers() - 1))) as ex:
            for res in ex.map(_scan, [(c, ws, me_serial, pets, area, kinds) for c, ws in todo.items()]):
                for r in res:
                    cache[f"{r['t0']:.1f}"] = r
        with open(cache_path, "w") as f:
            json.dump(cache, f)
    return [cache[f"{v['t_start']:.1f}"] for v in visits if f"{v['t_start']:.1f}" in cache]


# ---------------------------------------------------------------- measures
PLAYER_WHY = re.compile(r"\b(red|hostile) player\b|is attacking us \(server notice\)")


def per_visit(v: dict) -> dict:
    """One visit's numbers: hours on the level, its counters, leaves for hits, and the deaths the simulator can have:
    to monsters, on the walk out or not, but none in a visit left for a player (a red, a hostile player: the
    simulator has none; live two deaths to monsters came fleeing one)."""
    leaves = v.get("_leaves", [])
    deaths = v.get("_deaths", [])
    out = {"h": max(v["t_end"] - v["t_start"], 1.0) / 3600, **{k: v.get(k, 0) or 0 for k in COUNTS}}
    out["leaves_hits"] = sum(1 for lv in leaves if str(lv.get("why", "")).startswith("hits "))
    fled = any(PLAYER_WHY.search(str(lv.get("why", ""))) for lv in leaves)
    out["deaths_mob"] = 0 if fled else sum(1 for d in deaths if d.get("cause") != "pk")
    out["deaths_walk_out"] = 0 if fled else sum(1 for lv in leaves if lv.get("died"))
    return out


def rate(rows: list[dict], key: str) -> float:
    h = sum(r["h"] for r in rows)
    return sum(r[key] for r in rows) / h if h else 0.0


def boot(rows: list[dict], key: str, rng: random.Random) -> tuple[float, float]:
    """The 2.5 and 97.5 percentiles of the rate over resampled visits."""
    if not rows:
        return (0.0, 0.0)
    rs = sorted(rate([rng.choice(rows) for _ in rows], key) for _ in range(BOOT))
    return rs[int(0.025 * BOOT)], rs[int(0.975 * BOOT) - 1]


def sim_visits(results: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """The batch's visits (with their run's deaths and leaves on its last visit) and fights; world totals: the hours on
    the level, the damage on the pets and on us by source (the world's own accounting), the monsters met by type and
    the eerie corpses seen (runs with them on)."""
    visits, fights = [], []
    world = {"hunt_h": 0.0, "us": 0.0, "pets": {}, "us_by": {}, "met": {}, "eerie_seen": 0, "eerie_h": 0.0}
    for r in results:
        if "sim_s" not in r:
            continue
        vs = [dict(v) for v in r.get("visits") or []]
        for v in vs:
            v["_deaths"], v["_leaves"] = [], []
        if vs:
            # a run's deaths and leaves belong to its visits in order; each leave ends one, a death the last one
            for i, lv in enumerate(r.get("leave_events") or []):
                vs[min(i, len(vs) - 1)]["_leaves"].append(lv)
            vs[-1]["_deaths"].extend(r.get("death_events") or [])
        visits += vs
        fights += r.get("fights") or []
        world["hunt_h"] += r["hunt_s"] / 3600
        world["us"] += r["player_damage_taken"]
        for n, d in (r.get("pet_damage_taken") or {}).items():
            world["pets"][n] = world["pets"].get(n, 0) + d
        for key, src in (("us_by", r.get("player_damage_by")), ("met", r.get("met"))):
            for k, d in (src or {}).items():
                world[key][k] = world[key].get(k, 0) + d
        if r.get("eerie"):
            world["eerie_seen"] += r["eerie"]["eerie_seen"]
            world["eerie_h"] += r["hunt_s"] / 3600
    return visits, fights, world


def zone_shares(visits: list[dict]) -> dict[str, float]:
    """The share of the visits' crawl time in each zone (the crawl summary's `zones`)."""
    z = {}
    for v in visits:
        for k, d in ((v.get("crawl") or {}).get("zones") or {}).items():
            z[k] = z.get(k, 0.0) + d.get("s", 0.0)
    t = sum(z.values()) or 1e-9
    return {k: z[k] / t for k in sorted(z, key=int)}


def kill_times(fights: list[dict]) -> dict[str, list[float]]:
    out = {}
    for f in fights:
        if f.get("outcome") == "kill" and f.get("fight_s") is not None:
            out.setdefault(type_key(f.get("name")), []).append(f["fight_s"])
    return out


def report(live: list[dict], live_fights: list[dict], scan: list[dict], sim: list[dict], sim_fights: list[dict],
           world: dict, seed: int = 1) -> str:
    rng = random.Random(seed)
    L, S = [per_visit(v) for v in live], [per_visit(v) for v in sim]
    hl, hs = sum(r["h"] for r in L), sum(r["h"] for r in S)
    out = [f"live: {len(L)} visits, {hl:.2f} h on the level; sim: {len(S)} visits, {hs:.2f} h",
           f"{'per hour on the level':<24} {'live':>9} {'95 % interval':>19} {'sim':>9}  sim/live"]

    def line(k, rows, sv):
        lv, (lo, hi) = rate(rows, k), boot(rows, k, rng)
        flag = "<<" if sv < lo else ">>" if sv > hi else "  "
        out.append(f"{k:<24} {lv:>9.1f} {lo:>9.1f}-{hi:<9.1f} {sv:>9.1f} {flag} {sv / lv if lv else float('nan'):>5.2f}")
    for k in COUNTS + ["leaves_hits", "deaths_mob", "deaths_walk_out"]:
        line(k, L, rate(S, k))
    if world["eerie_h"]:
        for r, v in zip(L, live):
            r["eerie_seen"] = v.get("_eerie", 0)
        line("eerie_seen", [r for r, v in zip(L, live) if v["t_start"] >= EERIE_SINCE],
             world["eerie_seen"] / world["eerie_h"])
    zl, zs = zone_shares(live), zone_shares(sim)
    out.append("crawl time by zone, share: " + ", ".join(f"{k}: {zl.get(k, 0):.2f} / {zs.get(k, 0):.2f}"
                                                          for k in sorted(set(zl) | set(zs), key=int)) + " (live / sim)")
    if scan:
        # the scan's windows are the visits'; pair them with the visits for the bootstrap
        idx = {f"{v['t_start']:.1f}": i for i, v in enumerate(live)}
        rows = []
        for w in scan:
            i = idx.get(f"{w['t0']:.1f}")
            if i is not None:
                rows.append({"h": L[i]["h"], "us": sum(w["us_by"].values()),
                             **{f"pet:{n}": d for n, d in w["pets"].items()},
                             **{f"met:{k}": n for k, n in w["met"].items()}})
        wh = max(world["hunt_h"], 1e-9)
        keys = {k for r in rows for k in r if k != "h"} | {f"met:{k}" for k in world["met"]}
        for r in rows:
            for k in keys:
                r.setdefault(k, 0)
        out.append("damage on us and the pets (overhead numbers live, which match the status drops: 7,242 numbers to "
                   "7,222 hits lost on 35 visits; the world's accounting simulated), per hour on the level:")
        line("us", rows, world["us"] / wh)
        for k in sorted(k for k in keys if k.startswith("pet:")):
            line(k, rows, world["pets"].get(k[4:], 0) / wh)
        out.append("monsters met on the level (first sightings), per hour:")
        met = sorted((k for k in keys if k.startswith("met:")),
                     key=lambda k: -max(rate(rows, k), world["met"].get(k[4:], 0) / wh))
        for k in met[:24]:
            line(k, rows, world["met"].get(k[4:], 0) / wh)
        by_l, by_s = {}, world["us_by"]
        for w in scan:
            for k, d in w["us_by"].items():
                by_l[k] = by_l.get(k, 0) + d
        tl, ts = sum(by_l.values()) or 1e-9, sum(by_s.values()) or 1e-9
        top = sorted(set(by_l) | set(by_s), key=lambda k: -(by_l.get(k, 0) / tl + by_s.get(k, 0) / ts))
        out.append("damage on us by source, share (live: overhead numbers, the nearest monster in war mode; "
                   "the runner's hits_lost above is the comparable total):")
        out += [f"  {k:<22} {by_l.get(k, 0) / tl:>9.2f} {'':>19} {by_s.get(k, 0) / ts:>9.2f}" for k in top[:16]]
    kl, ks = kill_times(live_fights), kill_times(sim_fights)
    out.append("time to kill, median s (n): live / sim")
    for k in sorted(set(kl) & set(ks), key=lambda k: -len(kl[k])):
        if len(kl[k]) >= 5 and len(ks[k]) >= 5:
            out.append(f"  {k:<22} {statistics.median(kl[k]):>6.1f} ({len(kl[k]):>3}) / "
                       f"{statistics.median(ks[k]):>6.1f} ({len(ks[k]):>4})")
    return "\n".join(out)


def main(argv=None) -> int:
    import memory
    from sim.scenario import load
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sim", help="run.py --json output (one summary a line)")
    ap.add_argument("--db", default=memory.DEFAULT_DB, help="the live memory store (the main checkout's)")
    ap.add_argument("--logs", default=os.path.join(ROOT, "logs"), help="the live captures (session_*.jsonl/.raw)")
    ap.add_argument("--since", default=LIVE_SINCE, help="live visits from this local time on")
    ap.add_argument("--no-scan", action="store_true",
                    help="skip the capture scan (damage on the pets and on us by source, monsters met)")
    a = ap.parse_args(argv)
    sc = load()
    me = sc["player"]["serial"]
    pets = {p["serial"]: p["name"] for p in sc["pets"]}
    since = datetime.datetime.fromisoformat(a.since).timestamp()
    spots = sc["eerie"]["spots"]
    box = [min(s[0] for s in spots) - VIEW, min(s[1] for s in spots) - VIEW,
           max(s[0] for s in spots) + VIEW, max(s[1] for s in spots) + VIEW]
    live, live_fights = live_visits(a.db, since, me, box)
    scan = [] if a.no_scan else live_scan(a.logs, live, me, pets, sc["area"], set(sc["creatures"]))
    with open(a.sim, encoding="utf-8") as f:
        results = [json.loads(line) for line in f if line.strip().startswith("{")]
    sim, sim_fights, world = sim_visits(results)
    print(report(live, live_fights, scan, sim, sim_fights, world))
    return 0


if __name__ == "__main__":
    sys.exit(main())

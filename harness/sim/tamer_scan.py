"""Logan Wolf's Healer's Codex, Taming Bestiary loadouts and his pets' Guide values, read from the gumps in the memory
store (docs/SIM.md "Pets, teams and perks").

    python harness/sim/tamer_scan.py [--db PATH] [--out PATH]

The store keeps every gump the client opened as an `events` row (ev 'gump_open', data {gump_id, layout, lines, ...});
the sessions' capture tags come from `sessions.tag`. The store is opened read-only. Three gumps:

- the Healer's Codex (CODEX_GUMP), two pages of perk rows: 0-3 gems (tilepic 3617) then "N Pts" (text at x 70) then
  the perk's name (the next text at x 130); a perk's tier is its gem count, its points 0/1/3/5 for tiers 0-3 (a
  mismatch is a ValueError naming the row's session/seq). Page "k/2" at (213, 528), points spent "a/20" at (77, 605).
  The current codex merges the latest view of each page; a view with 0 points spent (a reset) empties both.
- the Taming Bestiary (BESTIARY_GUMP): per loadout and tab (Attack/Utility/Tank: the button drawn with gump 2154, ids
  10/11/12), 11 pages of 4 perk rows in the codex's row grammar (gems tilepic 55046; a "[Cross] " name is another
  tab's perk); page "k/11" at (215, 356), points spent "a/20" at (77, 605), cross-class "c/5" at (310, 547); the
  loadout viewed: the select's default item (select 96 704 252 <id> 1, selectitem <id> @Name@); the active one: the
  selectitem drawn with " 63 -1 -1". A tab is rebuilt from its views after the last one with 0 points spent (a Reset,
  or a new loadout), keeping the latest view of each page; its perks only when their points sum to the gump's points
  spent and their cross points to its cross-class count, else `decoded: false` (pages spent on before the captures
  began). A tab never viewed is null.
- the Guide (GUIDE_GUMP) of each pet in the roster (harness/data/sim/tamer/logan_wolf.json `pets`): its title
  ("Name the Species", or "A Species" for an unnamed pet) at y 40, "Level N", and the Values table (labels at x 75,
  values at x 170, by row y: GUIDE_ROWS); a wild creature's (with a Taming Chance) is skipped. The latest view wins.

Each value is read through its element's line index (`lines` holds each distinct string once), never by position.
Output (committed, like the scenarios' *_observed.json): harness/data/sim/tamer/logan_wolf_observed.json; a summary
table on stdout.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.dirname(HERE)
DB = os.path.join(HARNESS, "data", "harness.db")
TAMER_DIR = os.path.join(HARNESS, "data", "sim", "tamer")
ROSTER = os.path.join(TAMER_DIR, "logan_wolf.json")
OUT = os.path.join(TAMER_DIR, "logan_wolf_observed.json")

CODEX_GUMP = 1244441914
BESTIARY_GUMP = 1353229584
GUIDE_GUMP = 3280914284
CODEX_GEM = 3617
BESTIARY_GEM = 55046
TIER_POINTS = (0, 1, 3, 5)           # points spent on a perk at tier/rank 0-3 (1, then 2 a rank)
TABS = {"10": "attack", "11": "utility", "12": "tank"}
CROSS = "[Cross] "
ACTIVE_MARK = re.compile(r"selectitem \d+ @([^@]*)@ 63 -1 -1")
GUIDE_ROWS = {150: "min_taming", 170: "slots", 190: "hits", 210: "attack_s", 230: "damage", 250: "wrestling",
              270: "armor", 290: "magic_resist", 310: "poison_resist", 330: "special_resist"}


def elements(layout: str) -> list[list[str]]:
    """The layout's elements, each split on spaces ("text 70 59 2655 0 ..." -> ["text", "70", "59", ...])."""
    return [e.strip(" {").split() for e in layout.split("}") if e.strip(" {")]


def texts(els: list, lines: list) -> dict:
    """(x, y) -> the string a `text x y hue idx` element draws."""
    return {(int(e[1]), int(e[2])): lines[int(e[4])] for e in els if e[0] == "text" and int(e[4]) < len(lines)}


def _points(s: str) -> int:
    m = re.fullmatch(r"(\d+) Pts", s)
    if m is None:
        raise ValueError(f"not a points text: {s!r}")
    return int(m.group(1))


def _frac(s: str | None) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d+)/(\d+)", s or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def perk_rows(layout: str, lines: list, gem: int, where: str = "") -> list[dict]:
    """The perk rows of a codex or bestiary page: [{perk, points, rank}] in order. A row is its gems (tilepic `gem`),
    then its points text (x 70), then its name (the next text at x 130). ValueError when points and gems disagree."""
    rows, gems, pts = [], 0, None
    for e in elements(layout):
        if e[0] == "tilepic" and int(e[3]) == gem:
            gems += 1
        elif e[0] == "text" and e[1] == "70":
            pts = _points(lines[int(e[4])])
        elif e[0] == "text" and e[1] == "130" and pts is not None:
            if gems > 3 or TIER_POINTS[gems] != pts:
                raise ValueError(f"{where}: {lines[int(e[4])]!r} shows {gems} gems and {pts} points")
            rows.append({"perk": lines[int(e[4])], "points": pts, "rank": gems})
            pts, gems = None, 0
    return rows


def codex_rows(layout: str, lines: list, where: str = "") -> list[dict]:
    """A Healer's Codex page's rows: [{perk, points, tier}]."""
    return [{"perk": r["perk"], "points": r["points"], "tier": r["rank"]}
            for r in perk_rows(layout, lines, CODEX_GEM, where)]


def bestiary_rows(layout: str, lines: list, where: str = "") -> list[dict]:
    """A Taming Bestiary page's rows: [{perk, cross, points, rank}], the "[Cross] " prefix stripped into `cross`."""
    out = []
    for r in perk_rows(layout, lines, BESTIARY_GEM, where):
        cross = r["perk"].startswith(CROSS)
        out.append({"perk": r["perk"][len(CROSS):] if cross else r["perk"], "cross": cross,
                    "points": r["points"], "rank": r["rank"]})
    return out


def codex_view(layout: str, lines: list, where: str = "") -> dict:
    tx = texts(elements(layout), lines)
    return {"page": tx.get((213, 528)), "spent": _frac(tx.get((77, 605))), "rows": codex_rows(layout, lines, where)}


def bestiary_view(layout: str, lines: list, where: str = "") -> dict | None:
    """The loadout and tab a bestiary gump shows, its page, points spent, cross-class points, rows and the active
    loadout; None for a gump without a tab drawn."""
    els = elements(layout)
    tx = texts(els, lines)
    tab = next((TABS[e[-1]] for e in els if e[0] == "button" and len(e) > 3 and e[3] == "2154" and e[-1] in TABS), None)
    sel = re.search(r"\bselect 96 704 252 (\d+) 1", layout)
    items = dict(re.findall(r"selectitem (\d+) @([^@]*)@", layout))
    if tab is None or sel is None or sel.group(1) not in items:
        return None
    active = ACTIVE_MARK.findall(layout)
    return {"loadout": items[sel.group(1)], "tab": tab, "page": tx.get((215, 356)), "spent": _frac(tx.get((77, 605))),
            "cross": _frac(tx.get((310, 547))), "rows": bestiary_rows(layout, lines, where),
            "active": active[0] if active else None, "loadouts": sorted(set(items.values()))}


def _num(s: str | None) -> float | None:
    m = re.match(r"-?\d+(?:\.\d+)?", s or "")
    return float(m.group(0)) if m else None


def guide_view(layout: str, lines: list) -> dict | None:
    """A Guide gump's Values page: {title, level, wild, values...}; None for its other pages."""
    tx = texts(elements(layout), lines)
    title = next((v for (x, y), v in tx.items() if y == 40), None)
    level = next((int(m.group(1)) for v in tx.values() if (m := re.fullmatch(r"Level (\d+)", v))), None)
    if title is None or level is None or tx.get((75, 150)) != "Min Taming":
        return None
    out = {"title": title, "level": level, "wild": "Taming Chance" in tx.values()}
    for y, key in GUIDE_ROWS.items():
        v = tx.get((170, y))
        if key == "hits":
            out["hits_max"] = int(v.split("/")[1]) if v and "/" in v else None
        elif key == "damage":
            label = tx.get((75, y)) or ""
            out["damage_kind"] = "spell" if label.startswith("Spell") else "melee" if label.startswith("Melee") else None
            m = re.fullmatch(r"(\d+(?:\.\d+)?) - (\d+(?:\.\d+)?)", v or "")
            out["damage"] = [float(m.group(1)), float(m.group(2))] if m else None
        elif key == "slots":
            out["slots"] = int(v) if v and v.isdigit() else None
        else:
            out[key] = _num(v)
    return out


def _when(t: float) -> str:
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def _rows(con, ev: str, gumps: tuple) -> list:
    q = (f"SELECT e.session, e.seq, e.t, s.tag, e.data FROM events e JOIN sessions s ON s.id = e.session "
         f"WHERE e.ev = ? AND json_extract(e.data, '$.gump_id') IN ({','.join('?' * len(gumps))}) ORDER BY e.t, e.seq")
    return con.execute(q, (ev, *gumps)).fetchall()


def scan_codex(rows: list) -> dict:
    """The codex now ({perk: tier} for every perk), since when, its evidence and its history per capture session."""
    pages, history, last_sess = {}, [], None

    def merged():
        tiers = {r["perk"]: r["tier"] for p in sorted(pages) for r in pages[p][0]["rows"]}
        pts = sum(TIER_POINTS[v] for v in tiers.values())
        newest = max(pages.values(), key=lambda pv: pv[1]["t"])
        return tiers, pts, newest

    for sess, seq, t, tag, data in rows:
        d = json.loads(data)
        where = f"{sess}/{seq} {tag}"
        v = codex_view(d["layout"], d["lines"], where)
        if v["spent"] is not None and v["spent"][0] == 0:
            pages = {}
        if last_sess is not None and sess != last_sess[0] and pages:
            tiers, pts, newest = merged()
            history.append({"session": last_sess[0], "tag": last_sess[1], "t": newest[1]["t"], "codex": tiers,
                            "points": pts, "spent": list(newest[0]["spent"]) if newest[0]["spent"] else None})
        last_sess = (sess, tag)
        page = (_frac(v["page"]) or (0, 0))[0]
        pages[page] = (v, {"t": t, "evidence": f"{sess}/{seq} {tag} {_when(t)}"})
    if not pages:
        return {"current": {}, "since": None, "evidence": [], "history": history}
    tiers, pts, newest = merged()
    history.append({"session": last_sess[0], "tag": last_sess[1], "t": newest[1]["t"], "codex": tiers, "points": pts,
                    "spent": list(newest[0]["spent"]) if newest[0]["spent"] else None})
    return {"current": tiers, "since": newest[1]["t"], "since_text": _when(newest[1]["t"]), "points": pts,
            "spent": list(newest[0]["spent"]) if newest[0]["spent"] else None,
            "evidence": [f"page {p}: {pages[p][1]['evidence']}" for p in sorted(pages)], "history": history}


def scan_bestiary(rows: list) -> dict:
    """Every loadout's tabs rebuilt from their views ({perks, points, cross, decoded, evidence} or null) and the
    active loadout over time."""
    views, names, active = {}, set(), []
    for sess, seq, t, tag, data in rows:
        d = json.loads(data)
        v = bestiary_view(d["layout"], d["lines"], f"{sess}/{seq} {tag}")
        if v is None:
            continue
        v.update(t=t, evidence=f"{sess}/{seq} {tag} {_when(t)}")
        names.update(v["loadouts"])
        views.setdefault((v["loadout"], v["tab"]), []).append(v)
        if v["active"] and (not active or active[-1]["name"] != v["active"]):
            active.append({"name": v["active"], "from_t": t, "from": _when(t), "evidence": v["evidence"]})
    loadouts = {}
    for name in sorted(names):
        loadouts[name] = {}
        for tab in ("attack", "tank", "utility"):
            vs = views.get((name, tab))
            if not vs:
                loadouts[name][tab] = None
                continue
            cut = max((i for i, v in enumerate(vs) if v["spent"] and v["spent"][0] == 0), default=-1)
            latest = {}
            for v in vs[cut + 1:]:
                latest[v["page"]] = v
            perks, pts, cross = {}, 0, 0
            for v in latest.values():
                for r in v["rows"]:
                    if r["rank"]:
                        perks[(CROSS if r["cross"] else "") + r["perk"]] = r["rank"]
                        pts += r["points"]
                        cross += r["points"] if r["cross"] else 0
            last = vs[-1]
            spent = last["spent"][0] if last["spent"] else None
            xc = last["cross"][0] if last["cross"] else None
            ok = pts == spent and cross == xc
            loadouts[name][tab] = {"decoded": ok, "points": spent, "cross": xc, "pages_seen": len(latest),
                                   "evidence": f"latest {last['evidence']}; {len(vs)} views"}
            if ok:
                loadouts[name][tab]["perks"] = dict(sorted(perks.items()))
            else:
                loadouts[name][tab]["note"] = (f"the pages viewed since the last reset add to {pts} points ({cross} cross) "
                                               f"against the gump's {spent} ({xc}): pages spent on before the captures")
    return {"loadouts": loadouts, "active": active}


def scan_guide(rows: list, roster: list) -> dict:
    """The latest Values page of each roster pet's Guide."""
    out = {}
    for sess, seq, t, tag, data in rows:
        d = json.loads(data)
        g = guide_view(d["layout"], d["lines"])
        if g is None or g["wild"]:
            continue
        title = g["title"].lower()
        pet = next((p for p in roster if title in (f"{p['name']} the {p['species']}".lower(), p["name"].lower())), None)
        if pet is None:
            continue
        out[pet["name"]] = {"species": pet["species"], "level": g["level"],
                            **{k: g.get(k) for k in ("min_taming", "slots", "hits_max", "attack_s", "damage_kind",
                                                     "damage", "wrestling", "armor", "magic_resist", "poison_resist",
                                                     "special_resist")},
                            "evidence": f"{sess}/{seq} {tag} {_when(t)} ({g['title']})"}
    return dict(sorted(out.items()))


def scan(db: str = DB, roster_path: str = ROSTER, as_of: float | None = None) -> dict:
    """The codex, the loadouts and the Guide values from the store's gumps; with `as_of` (epoch) as they stood then
    (the gumps opened up to it): a loadout as it was when an anchor was measured (data/sim/tamer anchors.loadouts)."""
    with open(roster_path, encoding="utf-8") as f:
        roster = json.load(f)["pets"]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        upto = (lambda rows: rows) if as_of is None else (lambda rows: [r for r in rows if r[2] <= as_of])
        codex = scan_codex(upto(_rows(con, "gump_open", (CODEX_GUMP,))))
        bestiary = scan_bestiary(upto(_rows(con, "gump_open", (BESTIARY_GUMP,))))
        guide = scan_guide(upto(_rows(con, "gump_open", (GUIDE_GUMP,))), roster)
    finally:
        con.close()
    return {"note": "generated by harness/sim/tamer_scan.py from the memory store's gump_open rows (Healer's Codex "
                    f"{CODEX_GUMP}, Taming Bestiary {BESTIARY_GUMP}, Guide {GUIDE_GUMP}); evidence: session/seq, "
                    "capture tag, local time. Rerun after Logan views a gump in game.",
            "codex": codex, "bestiary": bestiary, "guide": guide}


def summary(out: dict) -> str:
    c = out["codex"]
    lines = [f"Healer's Codex since {c.get('since_text')} ({c.get('points')} points, gump {c.get('spent')}):",
             "  " + ", ".join(f"{k} {v}" for k, v in sorted(c["current"].items())),
             "  " + "; ".join(c["evidence"]), "", "Taming Bestiary (active now: "
             + (out["bestiary"]["active"][-1]["name"] if out["bestiary"]["active"] else "?") + "):"]
    for name, tabs in out["bestiary"]["loadouts"].items():
        for tab, v in tabs.items():
            if v is None:
                lines.append(f"  {name:22s} {tab:8s} null (never viewed)")
            elif not v["decoded"]:
                lines.append(f"  {name:22s} {tab:8s} decoded: false ({v['points']}/20, {v['cross']}/5)")
            else:
                perks = ", ".join(f"{k} {r}" for k, r in v["perks"].items()) or "empty"
                lines.append(f"  {name:22s} {tab:8s} {v['points']:>2}/20 {v['cross']}/5  {perks}")
    lines += ["", "Active loadout:"] + [f"  from {a['from']}: {a['name']}" for a in out["bestiary"]["active"]]
    lines += ["", f"{'Guide':16s} {'species':13s} {'lvl':>3} {'slots':>5} {'tame':>5} {'hits':>5} {'atk s':>5} "
                  f"{'damage':>18} {'armor':>6} {'MR':>6}"]
    for name, g in out["guide"].items():
        dmg = f"{g['damage_kind']} {g['damage'][0]}-{g['damage'][1]}" if g["damage"] else "?"
        lines.append(f"{name:16s} {g['species']:13s} {g['level']:>3} {g['slots']:>5} {g['min_taming']:>5.0f} "
                     f"{g['hits_max']:>5} {g['attack_s']:>5} {dmg:>18} {g['armor']:>6} {g['magic_resist']:>6}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DB, help="the memory store (opened read-only)")
    ap.add_argument("--out", default=None, help=f"default {OUT}; with --as-of nothing is written unless given")
    ap.add_argument("--as-of", default=None, metavar="'YYYY-MM-DD HH:MM'",
                    help="the gumps as they stood then (local time): the summary only, unless --out")
    a = ap.parse_args(argv)
    when = datetime.datetime.strptime(a.as_of, "%Y-%m-%d %H:%M").timestamp() if a.as_of else None
    out = scan(a.db, as_of=when)
    print(summary(out))
    path = a.out or (None if when is not None else OUT)
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
            f.write("\n")
        print(f"\nwrote {path}")


if __name__ == "__main__":
    main()

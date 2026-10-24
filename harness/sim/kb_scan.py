"""A scenario's wiki knowledge (harness/data/sim/<scenario>_kb.json) from the memory store's wiki rows.

    python harness/sim/kb_scan.py --location "Tidal Tomb" --out harness/data/sim/tidal_tomb_kb.json \
        [--observed harness/data/sim/tidal_tomb_observed.json] [--mechanic ID ...] [--db harness/data/harness.db]

Per creature of the wiki's Creatures List rows at `--location` ("Name: ...; Location: ...; Slayer: ...;" one line each,
labelled, so unambiguous where the creature's own page's second table row lists only its filled cells): the stats the
simulator reads (scenario.creature_spec: hits, melee damage, AI, spell damage, speed, difficulty, gold value) and the
rest the kb keeps (wrestling, armor, resists, parry, attack speed, poison, stealth, slayer, unique scaler). The
creature's own page adds its knowledge id. A tameable's follower abilities (Tameables List "CooldownAbility" /
"PassiveAbility" at that dungeon, described in "Follower Abilities > All Abilities") go into `specials` marked as
the tamed version's: that the wild creature uses one is [INFERENCE] until a capture shows it. `--observed`
(sim/calibrate.py's output) adds what the captures saw of each type (`capture`). `--mechanic ID` copies a knowledge
entry (patch notes) into `mechanics`. Same store in, same file out (sorted keys).
"""
import argparse
import json
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.dirname(HERE)
DEFAULT_DB = os.path.join(HARNESS, "data", "harness.db")

NUM = re.compile(r"^-?[\d,]+(\.\d+)?$")
RANGE = re.compile(r"^(\d+)\s*-\s*(\d+)$")
FIELDS = {      # Creatures List label -> kb key
    "Slayer": "slayer", "Difficulty": "difficulty", "Gold Value": "gold_value", "Hits": "hits",
    "Melee Damage": "melee_damage", "Wrestling": "wrestling", "Armor": "armor", "Magic Resist": "magic_resist",
    "Parry": "parry", "AtkSpd": "attack_speed", "Magery": "magery", "Spell Damage": "spell_damage",
    "Poison": "poison", "Poisoning": "poisoning", "Poison Resist": "poison_resist", "AI": "ai", "Speed": "speed",
    "Unique Scaler": "unique_scaler",
}


def value(label: str, raw: str):
    raw = raw.strip()
    if raw in ("?", ""):
        return None
    if (m := RANGE.match(raw)) is not None:
        return [int(m.group(1)), int(m.group(2))]
    if NUM.match(raw):
        v = float(raw.replace(",", ""))
        return int(v) if v.is_integer() and label not in ("Difficulty", "Unique Scaler") else v
    if label == "AI":
        return raw.lower()
    return raw


def article(name: str) -> str:
    return ("an " if name[:1].lower() in "aeiou" else "a ") + name.lower()


def creatures_list(con, location: str) -> dict:
    """{type key: (fields, knowledge id)} of the Creatures List rows at `location`."""
    out = {}
    for kid, content in con.execute("SELECT id, content FROM knowledge WHERE status='active' AND source_type='wiki' "
                                    "AND topic LIKE 'Creatures List (%' ORDER BY id"):
        for line in content.split("\n"):
            parts = dict(p.split(": ", 1) for p in line.split("; ") if ": " in p)
            if parts.get("Location") != location or "Name" not in parts:
                continue
            out[parts["Name"].lower()] = (parts, kid)
    return out


def follower_abilities(con) -> dict:
    """{ability name: (type, description, knowledge id)} from 'Follower Abilities > All Abilities (n/26)'."""
    out = {}
    for kid, content in con.execute("SELECT id, content FROM knowledge WHERE status='active' AND source_type='wiki' "
                                    "AND topic LIKE 'Follower Abilities > All Abilities%' ORDER BY id"):
        for line in content.split("\n"):
            cells = [c.strip() for c in line.split(" | ")]
            if len(cells) >= 3 and cells[1] in ("Cooldown", "Passive", "Innate"):
                out[cells[0]] = (cells[1], cells[2], kid)
    return out


def tameables(con, location: str) -> dict:
    """{type key: ([ability names], knowledge id)} of the Tameables List rows at dungeon `location`."""
    out = {}
    for kid, content in con.execute("SELECT id, content FROM knowledge WHERE status='active' AND source_type='wiki' "
                                    "AND topic LIKE 'Tameables List%' ORDER BY id"):
        for line in content.split("\n"):
            parts = dict(p.split(": ", 1) for p in line.split("; ") if ": " in p)
            if parts.get("Dungeon") != location or "Name" not in parts:
                continue
            names = [a.strip() for k in ("CooldownAbility", "PassiveAbility", "InnateAbility")
                     for a in (parts.get(k) or "").split(",") if a.strip()]
            out[parts["Name"].lower()] = (names, kid)
    return out


def build(con, location: str, observed: dict | None, mechanic_ids: list[int]) -> dict:
    pages = {t.lower(): kid for kid, t in con.execute(
        "SELECT id, topic FROM knowledge WHERE status='active' AND source_type='wiki' AND topic NOT LIKE '%>%'")}
    abilities, tame = follower_abilities(con), tameables(con, location)
    obs = (observed or {}).get("creatures") or {}
    out = {}
    for key, (parts, kid) in sorted(creatures_list(con, location).items()):
        e = {"name": article(parts["Name"]), "location": location}
        for label, k in FIELDS.items():
            e[k] = value(label, parts[label]) if label in parts else None
        e["stealth"] = "Stealth" in parts
        ids = [kid] + ([pages[key]] if key in pages else [])
        specials = []
        names, tkid = tame.get(key, ([], None))
        if names:
            ids.append(tkid)
        for a in names:
            if a in abilities:
                typ, desc, akid = abilities[a]
                ids.append(akid)
                specials.append(f"[tamed-version follower ability, knowledge #{akid}; that the wild creature uses it "
                                f"is [INFERENCE]] {a} ({typ}): {desc}")
        e["specials"] = specials
        e["knowledge_ids"] = sorted(set(ids))
        e["missing"] = [k for k in ("attack_speed", "speed") if e[k] is None]
        if key in obs:
            o = obs[key]
            e["graphic"] = o["graphics"][0] if o["graphics"] else None
            e["capture"] = {"tags": [c["tag"] for c in observed["source"]["captures"]], "serials": o["n_serials"],
                            "graphics": [f"0x{g:x}" for g in o["graphics"]], "deaths": o["deaths_seen"]}
        out[key] = e
    mech = {}
    for mid in mechanic_ids:
        row = con.execute("SELECT topic, content FROM knowledge WHERE id=?", (mid,)).fetchone()
        if row is None:
            raise SystemExit(f"--mechanic {mid}: no such knowledge entry")
        slug = re.sub(r"[^a-z0-9]+", "_", row[0].split(">")[-1].strip().lower()).strip("_")
        mech[slug] = {"knowledge_id": mid, "topic": row[0], "text": row[1]}
    return {"source": f"harness/sim/kb_scan.py --location {location!r} (wiki rows of the memory store)",
            "creatures": out, "mechanics": mech}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--location", required=True, help="the wiki's Location / Dungeon value, e.g. 'Tidal Tomb'")
    ap.add_argument("--out", required=True)
    ap.add_argument("--observed", help="sim/calibrate.py's output: each type's capture summary")
    ap.add_argument("--mechanic", type=int, action="append", default=[], help="a knowledge id to copy (repeat)")
    ap.add_argument("--db", default=DEFAULT_DB)
    a = ap.parse_args(argv)
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    observed = None
    if a.observed:
        with open(a.observed, encoding="utf-8") as f:
            observed = json.load(f)
    data = build(con, a.location, observed, a.mechanic)
    if not data["creatures"]:
        raise SystemExit(f"no Creatures List rows at location {a.location!r}")
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")
    print(json.dumps({"out": a.out, "creatures": len(data["creatures"]), "mechanics": len(data["mechanics"])}))


if __name__ == "__main__":
    main()

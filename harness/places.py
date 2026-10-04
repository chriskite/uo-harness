"""Named places to travel to (docs/research/WORLD_LOCATIONS.md).

- The Witcher rune system: harness/data/witcher_runes.json (committed): rune id
  -> name, tile and the Cambria Rune Library tome that holds it. A character
  that can recall stands within the library's use range of that tome and
  recalls with escape.recall(io, tome, rune=id).
- The World Atlas packs the client ships in ClassicUO/Data/Client/*.xml
  (read-only install data, like guards.bank_markers): points of interest,
  healer caravans, shrines, townships, moongates, dungeons.
"""
import json
import os
import re
import xml.etree.ElementTree as ET

import uomap

HERE = os.path.dirname(os.path.abspath(__file__))
WITCHER = os.path.join(HERE, "data", "witcher_runes.json")
CLIENT_DATA = os.path.join(uomap.INSTALL, "ClassicUO", "Data", "Client")
ATLAS = {"poi": "POI.xml", "caravan": "Healer_Caravans.xml", "shrine": "Shrines.xml",
         "town": "Townships.xml", "moongate": "Moongates.xml", "dungeon": "Dungeons.xml"}
# Place-name words that mean monsters live there (POI.xml and Witcher rune names, e.g. "Brigand
# Camp 1", "Orc Fort 2", "Ratman Hovel", "Necromancers Swamp") [INFERENCE from the names]
DANGER_WORDS = ("brigan", "orc", "lizardm", "savage", "daemon", "dragon", "necromancer", "ratman",
                "cemetery", "crypt", "graveyard", "ogre", "terathan", "harpy", "lair", "troll", "ettin",
                "cult", "undead", "spider", "arachnid", "minotaur", "kraul", "barbaric", "tribal", "smuggler",
                "bandit", "pirate", "haunted", "volcano", "tomb", "pits", "dungeon", "cavernam")
_WORD = re.compile(r"[a-z]+")

_witcher = None
_witcher_path = WITCHER


def use(path: str):
    """Read the Witcher table from `path` instead (offline tests: a simulated library)."""
    global _witcher, _witcher_path
    _witcher, _witcher_path = None, path


def witcher() -> dict:
    """The Witcher table (cached)."""
    global _witcher
    if _witcher is None:
        with open(_witcher_path, encoding="utf-8") as f:
            _witcher = json.load(f)
    return _witcher


def library(lib_id: str = "cambria") -> dict:
    lib = next((lb for lb in witcher()["libraries"] if lb["id"] == lib_id), None)
    if lib is None:
        raise KeyError(f"no rune library {lib_id!r}")
    return lib


def witcher_rune(rune_id: str) -> dict:
    """{id, name, x, y, tome} of a Witcher rune (KeyError if unknown)."""
    r = next((r for r in witcher()["runes"] if r["id"] == str(rune_id).strip().lower()), None)
    if r is None:
        raise KeyError(f"no Witcher rune {rune_id!r}")
    return r


def tome_at_hand(world: dict, pos, rune: dict, lib_id: str = "cambria") -> int | None:
    """The tome holding `rune` when the world model has it within the library's
    use range of pos, else None."""
    lib = library(lib_id)
    it = (world.get("items") or {}).get(rune["tome"])
    if it is None or it.get("x") is None or pos is None:
        return None
    if max(abs(it["x"] - pos[0]), abs(it["y"] - pos[1])) > lib["use_range"]:
        return None
    return int(rune["tome"], 16)


def atlas(kinds=tuple(ATLAS), root: str = CLIENT_DATA) -> list:
    """[{kind, name, x, y, facet}] from the client's Atlas marker packs; a missing
    or unreadable file contributes nothing."""
    out = []
    for kind in kinds:
        try:
            tree = ET.parse(os.path.join(root, ATLAS[kind])).getroot()
        except (OSError, ET.ParseError):
            continue
        for m in tree.iter("Marker"):
            try:
                out.append({"kind": kind, "name": m.get("Name") or "", "x": int(m.get("X")),
                            "y": int(m.get("Y")), "facet": int(m.get("Facet") or 0)})
            except (TypeError, ValueError):
                continue
    return out


def danger_hint(name: str) -> list:
    """DANGER_WORDS in a place name ([] = nothing known)."""
    words = _WORD.findall((name or "").lower())
    return sorted({d for d in DANGER_WORDS for w in words if w.startswith(d)})

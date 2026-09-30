"""Gump layout parsing (UO gump command syntax, `{ cmd args... }`).

Shared by the demonstration miner (loop_mine.py) and the loop runner
(loop_lumber.py): reply buttons, text entries, text-line references and
cliloc numbers of a layout string.
"""
import re

_ENTRY = re.compile(r"\{\s*([a-z]+)([^}]*)\}", re.I)


def _ints(s):
    return [int(x) for x in re.findall(r"-?\d+", s)]


def parse_layout(layout: str) -> dict:
    """{"buttons": reply-button ids, "entries": text-entry ids,
    "line_refs": text-line indexes, "clilocs": cliloc numbers}."""
    out = {"buttons": [], "entries": [], "line_refs": [], "clilocs": []}
    for cmd, rest in _ENTRY.findall(layout or ""):
        cmd, n = cmd.lower(), _ints(rest)
        if cmd in ("button", "buttontileart") and len(n) >= 7 and n[4] == 1:
            out["buttons"].append(n[6])          # x y up down quit=1 page id
        elif cmd in ("textentry", "textentrylimited") and len(n) >= 6:
            out["entries"].append(n[5])          # x y w h hue id ...
        elif cmd in ("text", "croppedtext") and n:
            out["line_refs"].append(n[-1])
        elif cmd == "htmlgump" and len(n) >= 5:
            out["line_refs"].append(n[4])
        elif cmd.startswith("xmfhtml") and len(n) >= 5:
            out["clilocs"].append(n[4])          # x y w h cliloc ...
        elif cmd == "tooltip" and n:
            out["clilocs"].append(n[0])
    return out

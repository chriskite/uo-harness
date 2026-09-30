"""Gump layout parsing (UO gump command syntax, `{ cmd args... }`).

Shared by the demonstration miner (loop_mine.py), the loop runner
(loop_lumber.py) and ctl (gump views): reply buttons, text entries, text-line
references, cliloc numbers, and (controls) where each button and text entry
sits with the texts next to it.

Argument layouts (ClassicUO GumpBuilder; Outlands appends extra fields to
`text`, e.g. `{ text 82 40 2655 2 18 0 1 0 0 0 }`, so the line index is a
fixed position, not the last number):
  button / buttontileart   x y up down type page id      (type 1 = reply, 0 = page)
  text                     x y hue line
  croppedtext              x y w h hue line
  htmlgump                 x y w h line bg scroll
  xmfhtmlgump[color]       x y w h cliloc bg scroll [color]
  xmfhtmltok               x y w h bg scroll color cliloc [args]
  textentry[limited]       x y w h hue id line [limit]
  page                     n
"""
import re

_ENTRY = re.compile(r"\{\s*([a-z]+)([^}]*)\}", re.I)
_TAG = re.compile(r"<[^>]*>")
NEAR_DY = 12            # a label is on the control's row within this many px
NEAR_DX = 150           # ... and within this many px left or right of it
NEAR_MAX = 3


def _ints(s):
    return [int(x) for x in re.findall(r"-?\d+", s)]


def parse_layout(layout: str) -> dict:
    """{"buttons": reply-button ids, "entries": text-entry ids,
    "line_refs": text-line indexes, "clilocs": cliloc numbers}."""
    out = {"buttons": [], "entries": [], "line_refs": [], "clilocs": []}
    for cmd, rest in _ENTRY.findall(layout or ""):
        cmd, n = cmd.lower(), _ints(rest)
        if cmd in ("button", "buttontileart") and len(n) >= 7 and n[4] == 1:
            out["buttons"].append(n[6])
        elif cmd in ("textentry", "textentrylimited") and len(n) >= 6:
            out["entries"].append(n[5])
        elif cmd == "text" and len(n) >= 4:
            out["line_refs"].append(n[3])
        elif cmd == "croppedtext" and len(n) >= 6:
            out["line_refs"].append(n[5])
        elif cmd == "htmlgump" and len(n) >= 5:
            out["line_refs"].append(n[4])
        elif cmd == "xmfhtmltok" and len(n) >= 8:
            out["clilocs"].append(n[7])
        elif cmd.startswith("xmfhtml") and len(n) >= 5:
            out["clilocs"].append(n[4])
        elif cmd == "tooltip" and n:
            out["clilocs"].append(n[0])
    return out


def reply_fields(layout: str, lines=()) -> tuple[list, list]:
    """(text entries [(id, value)], switches [ids]) a stock client sends when a
    button is pressed without the player touching anything: every text entry in
    layout order with its current (default) text, and every checkbox/radio that
    starts checked (ClassicUO Gump.OnButtonClick: all StbTextBox children, all
    checked Checkbox children). `checkbox|radio x y off on state id`."""
    lines = list(lines or ())
    entries, switches = [], []
    for cmd, rest in _ENTRY.findall(layout or ""):
        cmd, n = cmd.lower(), _ints(rest)
        if cmd in ("textentry", "textentrylimited") and len(n) >= 7:
            entries.append((n[5], lines[n[6]] if 0 <= n[6] < len(lines) else ""))
        elif cmd in ("checkbox", "radio") and len(n) >= 6 and n[4]:
            switches.append(n[5])
    return entries, switches


def controls(layout: str, lines=(), cliloc_text=None) -> dict:
    """Where each reply button and text entry is, with the texts on its row:
    {"buttons": [{id, x, y, page, near}], "entries": [{id, x, y, page, value,
    limit, near}]}. `near` = up to NEAR_MAX texts within NEAR_DY rows and
    NEAR_DX px, nearest first, as {text, dx, dy} (dx > 0: the text is right of
    the control). Labels are geometry, not semantics: read `near` with dx.
    cliloc_text(n) renders cliloc-based texts (else "#n")."""
    lines = list(lines or ())
    line = lambda i: lines[i] if 0 <= i < len(lines) else ""  # noqa: E731
    render = cliloc_text or (lambda n: f"#{n}")
    texts, buttons, entries, page = [], [], [], 0
    for cmd, rest in _ENTRY.findall(layout or ""):
        cmd, n = cmd.lower(), _ints(rest)
        if cmd == "page" and n:
            page = n[0]
        elif cmd in ("button", "buttontileart") and len(n) >= 7 and n[4] == 1:
            buttons.append({"id": n[6], "x": n[0], "y": n[1], "page": page})
        elif cmd in ("textentry", "textentrylimited") and len(n) >= 7:
            e = {"id": n[5], "x": n[0], "y": n[1], "page": page, "value": line(n[6])}
            if cmd == "textentrylimited" and len(n) >= 8:
                e["limit"] = n[7]
            entries.append(e)
        elif cmd == "text" and len(n) >= 4:
            texts.append((n[0], n[1], line(n[3])))
        elif cmd == "croppedtext" and len(n) >= 6:
            texts.append((n[0], n[1], line(n[5])))
        elif cmd == "htmlgump" and len(n) >= 5:
            texts.append((n[0], n[1], _TAG.sub("", line(n[4]))))
        elif cmd == "xmfhtmltok" and len(n) >= 8:
            texts.append((n[0], n[1], _TAG.sub("", render(n[7]))))
        elif cmd.startswith("xmfhtml") and len(n) >= 5:
            texts.append((n[0], n[1], _TAG.sub("", render(n[4]))))
    texts = [(x, y, t.strip()) for x, y, t in texts if t and t.strip()]

    def near(cx, cy):
        cand = [(abs(x - cx), t, x - cx, y - cy) for x, y, t in texts
                if abs(y - cy) <= NEAR_DY and abs(x - cx) <= NEAR_DX]
        return [{"text": t, "dx": dx, "dy": dy} for _, t, dx, dy in sorted(cand)[:NEAR_MAX]]
    for c in buttons + entries:
        c["near"] = near(c["x"], c["y"])
    return {"buttons": buttons, "entries": entries}

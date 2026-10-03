"""Recall to the default rune of a runebook or rune tome: the escape a job runs
when a red shows up (docs/PLAN.md "Red sighting: recall at once").

The flows are the ones the Outlands server offers to a player who double-clicks
the book (captured live 2026-10-02 on the Test Shard; docs/NOTES.md "Runebook and
rune tome gumps"):

  runebook  gump 0x5C7DB029. Entry i: 2+6i recall with a charge, 3+6i drop the
            rune, 4+6i set default, 5+6i cast Recall, 6+6i cast Gate. The default
            entry's set-default button shows gump art 2360 (others 2361).
            "Charges: " is followed by the count.
  runetome  gump 0x09F5976B. Main page row i: 100+i recall with a charge (no
            charge: "That rune tome is out of recall charges."), 200+i the rune's
            detail page. The default row's name is drawn in hue 63 (others 2655).
            The recall charges are the text right of the recall icon (art 2271).
            The detail page shows runes in pairs, each with a recall-spell icon
            button (art 2271): left column = even rune, right = odd.

Charges first (no mana, no reagents), else the spell. No pause is added before
the press (user decision 2026-10-02): the reaction is the book's round trip.
The server holds you still while casting, so nothing else is sent until the
result: arrival (a jump of the own position), or a failure message.

The IO object hides who talks to the proxy: `send(pkt)` raises RecallError when
the proxy refuses; `poll()` returns (full state, world events since the last
poll). LinkIO wraps a runner's agent_link.Link; ctl has its own.
"""

from __future__ import annotations

import re
import time

import actions
import combat
import uomap
from agent_link import cheb, serial_of

RUNEBOOK_GUMP = 0x5C7DB029
RUNETOME_GUMP = 0x09F5976B
RUNEBOOK_DEFAULT_ART = 2360       # set-default button on the default entry (others 2361)
RUNETOME_DEFAULT_HUE = 63         # the default rune's name on the tome's main page (others 2655)
RECALL_ICON_ART = 2271            # recall-spell icon (both books)
LAYER_BACKPACK = 0x15
RECALL = combat.spell_id("recall")
RECALL_MANA = combat.spell_mana(RECALL)   # 4th circle: 11; TestWorth spent 10 live
GUMP_WAIT_S = 2.0                 # double-click -> the book's gump (48 ms live)
ARRIVE_WAIT_S = 5.0               # press -> arrival (2.05-2.09 s live)
JUMP_TILES = 2                    # an own-position change this large while frozen = arrived
RECLICK_S = 0.6                   # no gump this long after the double-click: click once more

# Failure messages (cliloc ids from the local Cliloc.enu, docs/research/TRAVEL_DEATH.md §1.2;
# the tome's text seen live). Any of them ends the attempt.
FAIL_CLILOCS = {
    500641: "disturbed",           # Your concentration is disturbed, thus ruining thy spell.
    502632: "fizzled",             # The spell fizzles.
    502630: "reagents",            # More reagents are needed for this spell.
    502625: "mana",                # Insufficient mana for this spell.
    502644: "not recovered",       # You have not yet recovered from casting a spell.
    1005564: "heat of battle",     # Wouldst thou flee during the heat of battle??
    502412: "no charges",          # There are no charges left on that item.
    502403: "recharging",          # This book needs time to recharge.
    501025: "blocked",             # Something is blocking the location.
    501803: "unmarked",            # That rune is not yet marked.
}
FAIL_TEXTS = {
    "That rune tome is out of recall charges.": "no charges",
    "Your concentration is disturbed, thus ruining thy spell.": "disturbed",
    "The spell fizzles.": "fizzled",
    "More reagents are needed for this spell.": "reagents",
    "Insufficient mana for this spell.": "mana",
    "You have not yet recovered from casting a spell.": "not recovered",
}


class RecallError(Exception):
    """The escape can't be tried (no book, no default, refused by the proxy)."""


def book_kind(world: dict, serial: int) -> str | None:
    """'runebook' or 'runetome' by the item's tiledata name, else None."""
    ent = world["items"].get(f"0x{serial:08X}") or {}
    if ent.get("graphic") is None:
        return None
    name = (uomap.tiledata().item(serial_of(ent["graphic"])).name or "").lower().replace(" ", "")
    return name if name in ("runebook", "runetome") else None


def backpack(world: dict, me: int) -> int | None:
    for key, it in world["items"].items():
        if it.get("layer") == LAYER_BACKPACK and it.get("container") is not None \
                and serial_of(it["container"]) == me:
            return serial_of(key)
    return None


def in_pack(world: dict, serial: int, pack: int) -> bool:
    """`serial` sits in the backpack at any bag depth."""
    items, seen = world["items"], set()
    ent = items.get(f"0x{serial:08X}")
    while ent is not None and ent.get("container") is not None:
        parent = serial_of(ent["container"])
        if parent == pack:
            return True
        if parent in seen:
            return False
        seen.add(parent)
        ent = items.get(f"0x{parent:08X}")
    return False


def find_books(world: dict, me: int) -> list[tuple[int, str]]:
    """[(serial, kind)] of the runebooks and rune tomes in the backpack, tomes first
    (more charges), then by serial."""
    pack = backpack(world, me)
    if pack is None:
        return []
    out = []
    for key in world["items"]:
        s = serial_of(key)
        kind = book_kind(world, s)
        if kind and in_pack(world, s, pack):
            out.append((s, kind))
    return sorted(out, key=lambda b: (b[1] != "runetome", b[0]))


def can_cast_recall(world: dict, me: int, mana: int | None) -> bool:
    """Mana for Recall plus its reagents, or a spellstone, in the backpack
    (combat.can_cast). Whether the spellbook holds Recall isn't read: the server
    says so."""
    return combat.can_cast(world, me, RECALL, mana)


# ------------------------------------------------------------ layout parsing
_TOKEN = re.compile(r"\{([^}]*)\}")


def _tokens(layout: str):
    for t in _TOKEN.findall(layout or ""):
        f = t.split()
        if f:
            yield f[0].lower(), f[1:]


def parse_runebook(layout: str, lines) -> dict:
    """{'default': entry index or None, 'charges': int, 'entries': int}."""
    lines = list(lines or [])
    default, ids = None, set()
    for kind, f in _tokens(layout):
        if kind == "button" and len(f) >= 7:
            art, bid = int(f[2]), int(f[6])
            if bid >= 2 and (bid - 2) % 6 == 2 and art == RUNEBOOK_DEFAULT_ART:
                default = (bid - 4) // 6
            if bid >= 2 and (bid - 2) % 6 == 3 and art == RECALL_ICON_ART:
                ids.add((bid - 5) // 6)        # a recall-spell icon = a filled entry
    charges = 0
    if "Charges: " in lines:
        i = lines.index("Charges: ")
        if i + 1 < len(lines) and lines[i + 1].strip().isdigit():
            charges = int(lines[i + 1])
    return {"default": default, "charges": charges, "entries": len(ids)}


def _runetome_row_texts(layout: str) -> dict:
    """{row index: (line index, hue)}: the text on the same line as the row's gem
    button (100 + row), right of it. A full tome draws two columns at the same
    heights, so rows are matched by position, not by height alone."""
    gems, texts = {}, []
    for kind, f in _tokens(layout):
        if kind == "button" and len(f) >= 7 and 100 <= int(f[6]) < 200:
            gems[int(f[6]) - 100] = (int(f[0]), int(f[1]))
        elif kind == "text" and len(f) >= 4:
            texts.append((int(f[0]), int(f[1]), int(f[2]), int(f[3])))
    out = {}
    for row, (bx, by) in gems.items():
        near = [(x - bx, li, hue) for x, y, hue, li in texts if x > bx and abs(y - by) <= 6]
        if near:
            _, li, hue = min(near)
            out[row] = (li, hue)
    return out


def parse_runetome_main(layout: str, lines) -> dict:
    """{'default': row index or None, 'charges': int, 'entries': int} from the
    tome's main page (the one with "Manage Runes")."""
    lines = list(lines or [])
    rows = _runetome_row_texts(layout)
    entries = sum(1 for kind, f in _tokens(layout) if kind == "button" and len(f) >= 7 and 100 <= int(f[6]) < 200)
    default = next((row for row, (li, hue) in sorted(rows.items()) if hue == RUNETOME_DEFAULT_HUE), None)
    texts, icon_x = [], None
    for kind, f in _tokens(layout):
        if kind == "text" and len(f) >= 4:
            texts.append((int(f[0]), int(f[1]), int(f[3])))
        elif kind == "gumppic" and len(f) >= 3 and int(f[2]) == RECALL_ICON_ART:
            icon_x = int(f[0])
    charges = 0
    if icon_x is not None:
        right = [(x, li) for x, y, li in texts if 0 < x - icon_x < 120 and y < 50]
        for _, li in sorted(right):
            m = re.match(r"\s*(\d+)\s*/", lines[li]) if li < len(lines) else None
            if m:
                charges = int(m.group(1))
                break
    return {"default": default, "charges": charges, "entries": entries}


def runetome_rows(layout: str, lines) -> dict:
    """{row index: rune name} from a tome's main page."""
    lines = list(lines or [])
    return {row: lines[li] for row, (li, _) in _runetome_row_texts(layout).items() if li < len(lines)}


def rune_matches(name: str, want: str) -> bool:
    """A tome row named `name` is the wanted rune: the same text, or a Witcher
    row "N - Place" for want "N"."""
    return name == want or name.startswith(f"{want} - ")


def runetome_cast_button(layout: str, index: int) -> int | None:
    """The tome detail page's Cast Recall button for rune `index` (pairs: even left)."""
    icons = sorted((int(f[0]), int(f[6])) for kind, f in _tokens(layout)
                   if kind == "button" and len(f) >= 7 and int(f[2]) == RECALL_ICON_ART)
    if not icons:
        return None
    col = index % 2
    return icons[col][1] if col < len(icons) else None


# ------------------------------------------------------------ the escape
def failure(ev: dict, me: int) -> str | None:
    """The failure an event reports, else None."""
    if ev.get("ev") == "cliloc" and ev.get("cliloc") in FAIL_CLILOCS:
        return FAIL_CLILOCS[ev["cliloc"]]
    if ev.get("ev") == "speech_heard" and ev.get("text") in FAIL_TEXTS:
        return FAIL_TEXTS[ev["text"]]
    return None


class LinkIO:
    """escape IO over a runner's agent_link.Link (Link.act waits out a paused gate)."""

    def __init__(self, link):
        self.link = link
        self.cursor = len(link.events)

    def send(self, pkt: bytes):
        self.link.act(pkt)

    def poll(self):
        st = self.link.state()
        new = self.link.events[self.cursor:]
        self.cursor = len(self.link.events)
        return st, new


def _open(io, book: int, gump_id: int, me: int, timeout: float = GUMP_WAIT_S) -> dict:
    """Double-click the book; the gump_open event of its gump (layout, lines, serial).
    A double-click right after another action is ignored by the server (seen live:
    the escape's click 0.3 s after the readiness check's), so one more after
    RECLICK_S without a gump."""
    io.poll()
    io.send(actions.dclick(book))
    t0 = time.monotonic()
    reclicked = False
    while time.monotonic() - t0 < timeout:
        _, evs = io.poll()
        for ev in evs:
            if ev.get("ev") == "gump_open" and serial_of(ev["gump_id"]) == gump_id:
                return ev
        if not reclicked and time.monotonic() - t0 > RECLICK_S:
            io.send(actions.dclick(book))
            reclicked = True
        time.sleep(0.03)
    raise RecallError(f"the book's gump 0x{gump_id:08X} didn't open")


def _press(io, g: dict, button: int):
    io.send(actions.gump_reply(serial_of(g["serial"]), serial_of(g["gump_id"]), button,
                               g.get("layout") or "", g.get("lines") or []))


def _next_gump(io, gump_id: int, timeout: float = GUMP_WAIT_S) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _, evs = io.poll()
        for ev in evs:
            if ev.get("ev") == "gump_open" and serial_of(ev["gump_id"]) == gump_id:
                return ev
        time.sleep(0.03)
    raise RecallError(f"gump 0x{gump_id:08X} didn't come back")


def recall(io, book: int, *, prefer: str = "charge", rune: str | None = None,
           timeout: float = ARRIVE_WAIT_S) -> dict:
    """One recall with `book`: to its default rune, or (rune tomes) to the row
    named `rune` (rune_matches: a Witcher number like "286" finds "286 - Midlands
    Ruins 1 (South)"); the book may be a locked-down library tome within reach.
    Returns {ok, kind, method, rune, name, from, to, elapsed_s, failure,
    charges}; raises RecallError when it can't be tried."""
    st, _ = io.poll()
    me = st["movement"]["self_serial"]
    world = st["world"]
    kind = book_kind(world, book)
    if kind is None:
        raise RecallError(f"0x{book:08X} is not a runebook or rune tome")
    mana = (world.get("self") or {}).get("mana")
    start = tuple(st["movement"]["pos"][:2])
    facet = (world.get("self") or {}).get("map")
    t0 = time.monotonic()
    name = None
    if kind == "runebook":
        g = _open(io, book, RUNEBOOK_GUMP, me)
        info = parse_runebook(g.get("layout"), g.get("lines"))
        if rune is not None:
            _press(io, g, 0)
            raise RecallError("picking a rune by name works for rune tomes only")
        rune = info["default"] if info["default"] is not None else (0 if info["entries"] == 1 else None)
        if rune is None:
            _press(io, g, 0)
            raise RecallError("the runebook has no default rune")
        method = "charge" if info["charges"] > 0 and prefer == "charge" else "spell"
        if method == "spell" and not can_cast_recall(world, me, mana) and info["charges"] > 0:
            method = "charge"
        _press(io, g, 2 + 6 * rune if method == "charge" else 5 + 6 * rune)
    else:
        g = _open(io, book, RUNETOME_GUMP, me)
        info = parse_runetome_main(g.get("layout"), g.get("lines"))
        rows = runetome_rows(g.get("layout"), g.get("lines"))
        if rune is not None:
            index = next((i for i, n in sorted(rows.items()) if rune_matches(n, rune)), None)
            if index is None:
                _press(io, g, 0)
                raise RecallError(f"no rune {rune!r} in the rune tome 0x{book:08X}")
        else:
            index = info["default"] if info["default"] is not None else (0 if info["entries"] == 1 else None)
            if index is None:
                _press(io, g, 0)
                raise RecallError("the rune tome has no default rune")
        rune, name = index, rows.get(index)
        method = "charge" if info["charges"] > 0 and prefer == "charge" else "spell"
        if method == "spell" and not can_cast_recall(world, me, mana) and info["charges"] > 0:
            method = "charge"
        if method == "charge":
            _press(io, g, 100 + rune)
        else:
            _press(io, g, 200 + rune)
            d = _next_gump(io, RUNETOME_GUMP)
            button = runetome_cast_button(d.get("layout"), rune)
            if button is None:
                _press(io, d, 0)
                raise RecallError("the rune tome's detail page has no Cast Recall button")
            _press(io, d, button)
    pressed = time.monotonic()
    end = pressed + timeout
    why = None
    while time.monotonic() < end:
        st, evs = io.poll()
        for ev in evs:
            why = why or failure(ev, me)
        pos = st["movement"]["pos"]
        moved = pos is not None and cheb(start, pos[:2]) >= JUMP_TILES
        new_facet = (st["world"].get("self") or {}).get("map")
        if moved or (facet is not None and new_facet not in (None, facet)):
            return {"ok": True, "kind": kind, "method": method, "rune": rune, "name": name, "from": list(start),
                    "to": list(pos[:2]) if pos else None, "elapsed_s": round(time.monotonic() - t0, 2),
                    "press_to_arrival_s": round(time.monotonic() - pressed, 2),
                    "charges": info["charges"], "failure": None}
        if why:
            break
        time.sleep(0.05)
    return {"ok": False, "kind": kind, "method": method, "rune": rune, "name": name, "from": list(start), "to": None,
            "elapsed_s": round(time.monotonic() - t0, 2), "charges": info["charges"],
            "failure": why or "no arrival"}


def escape(io, book: int, *, attempts: int = 3, log=print, rune: str | None = None) -> dict:
    """Recall until it lands, at most `attempts` casts. Retries at once: a
    disturbed or fizzled cast can be recast (when hamstrung, running is pointless
    anyway: docs/PLAN.md); 'not recovered' waits a moment; out of charges falls
    back to the spell. `rune`: a tome row by name (recall()), else the default.
    Returns the last recall() result plus 'attempts' and 'tries' (every attempt's
    method, ok, failure and elapsed_s: the travel record of what each cast cost)."""
    prefer, last, tries = "charge", None, []
    for n in range(1, attempts + 1):
        last = recall(io, book, prefer=prefer, rune=rune)
        last["attempts"] = n
        tries.append({k: last[k] for k in ("method", "ok", "failure", "elapsed_s")})
        last["tries"] = tries
        log(f"recall {n}/{attempts}: {'arrived' if last['ok'] else last['failure']} "
            f"({last['kind']}, {last['method']}, rune {last['name'] or last['rune'] + 1}, {last['elapsed_s']} s)")
        if last["ok"]:
            return last
        if last["failure"] in ("no charges", "recharging"):
            prefer = "spell"
        elif last["failure"] == "not recovered":
            time.sleep(0.3)
        elif last["failure"] in ("heat of battle", "unmarked", "blocked", "reagents", "mana") \
                and last["method"] == "spell":
            break
    return last


def check_ready(io, book: int) -> dict:
    """Open the book and read it (no recall): {kind, default, charges, entries,
    can_cast}. Closes the gump again, as a player glancing at their book."""
    st, _ = io.poll()
    me, world = st["movement"]["self_serial"], st["world"]
    kind = book_kind(world, book)
    if kind is None:
        raise RecallError(f"0x{book:08X} is not a runebook or rune tome")
    g = _open(io, book, RUNEBOOK_GUMP if kind == "runebook" else RUNETOME_GUMP, me)
    info = (parse_runebook if kind == "runebook" else parse_runetome_main)(g.get("layout"), g.get("lines"))
    _press(io, g, 0)
    info["kind"] = kind
    info["can_cast"] = can_cast_recall(world, me, (world.get("self") or {}).get("mana"))
    return info

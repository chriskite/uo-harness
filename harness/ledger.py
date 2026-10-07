"""Backpack ledger: inventory accounting from successive state snapshots.

    led = Ledger(woods=None)       # loads harness/data/woods.json when present
    delta = led.observe(state, expected=(), now=None) -> Delta
    led.expect(*entries, now=None)                     # declare causes ahead
    woods_summary(items, *, kind=None, woods=None) -> {wood_name: count}
    load_woods(path=WOODS_JSON) -> Woods
    classify(item, woods) -> ("log" | "board", wood_name) | None

`state` is a proxy state-port response (`world.items`, `world.self`,
`movement.self_serial`). The backpack is the item on layer 0x15 (ClassicUO
Layers.cs:28) parented to self, as in loop_lumber.Runner.backpack. Its
contents include nested containers.

Expected changes (`expected=` or `expect()`) are what the agent itself did:
    ("moved_out", serial)            the whole item may leave (store, drop, give)
    ("consumed", serial)             used up, e.g. a log stack cut into boards
    ("consumed", serial, amount)     up to `amount` of that stack may go
    ("spent", graphic)               any stack of this graphic may shrink or go
    ("spent", graphic, amount)       up to `amount` units of this graphic
    ("moving", serial, container)    a drag within the pack into `container` (logs into the
                                     trapped pouch): the stack may vanish while on the cursor
                                     (the server deletes it on the lift) or by merging into a
                                     stack in `container`; resolved once a view shows it there
Expectations persist until used up, resolved or `expect_ttl_s` (default 30 s) passes.
The runner may declare them before the server confirms the move. Nested containers count as
carried: a stack moved into a pouch in the pack is no loss, and a loss out of the pouch is
classified like any other (a thief's grab from it is `unexplained`).

Settling: an item that leaves the pack whole with no cause stays in the baseline for
`settle_s` (default 1 s) from the first view without it, and is booked only if a view after
that still misses it. The server moves an item in two packets and a view can fall between
them: a packed hatchet used is removed (0x1D) and then worn (0x2E) 16 ms later (live
2026-10-06, run lumber-20261006-173734-54c3: a view in between booked the hatchet as theft).
Turned up on the character it is `equipped`, back in the pack nothing; still gone after the
window it is classified as below (a thief's grab is booked one window late). A stack
decrease is booked at once.

Classification of each loss (Delta.lost[i]["cause"]):
    equipped      it went onto the character (container = self), e.g. the
                  hatchet. Never theft.
    expected      covered by an expectation
    merged        [HEURISTIC] a vanished stack's units reappeared in another
                  stack of the same (graphic, hue) in the same observation
    death         the character died (see below): its pack goes to the corpse
    unexplained   none of the above, while alive: possible theft. These are
                  also listed in Delta.unexplained_losses.
When a container inside the pack leaves as a whole, its contents are folded
into its entry (`contents`: item count) rather than reported one by one.
Entries carry serial, graphic, hue, amount, cause, `to` (the new container
serial as hex, "ground", "gone" when deleted, or "split" for a stack
decrease) and, for wood, `class` ("log"/"board") and `wood`.

Death, any of:
    - the self body is a ghost: 0x192/0x193/0x25F/0x260/0x2B6/0x2B7, per
      ClassicUO Mobile.IsDead (GameObjects/Mobile.cs:132-140). The world model
      keeps the self body in world.self.stats.graphic (0x1B / self 0x20).
    - self hits == 0
    - [HEURISTIC] a lost item now sits in a corpse (graphic 0x2006, the corpse
      art in the captured cliloc 1046414 "the remains of ~1_NAME~" packet)
    - [HEURISTIC] a mass vanish: >= 2 items, >= 80 % of the pack's top-level
      items, gone unexplained in one observation. [INFERENCE] A thief takes
      one item per attempt (RunUO Stealing; Outlands unverified).
While dead, every loss is `death`. The baseline follows the pack. A confirmed
death (ghost body or hits 0) ends when the body is back to normal. A heuristic
death ends after `death_hold_s` (60 s) unless a ghost body confirms it.

Wood classification uses harness/data/woods.json (contract: name,
log_graphics, log_hue, board_hue). Without the file or a matching hue the wood
is `unknown(<hue>)` with the hue in decimal, as in the file. The log and board
graphics are 0x1BDD-0x1BE2 and 0x1BD7 (loop_lumber.LOGS / BOARDS; the
captured demonstration 20260929_204225), plus every log_graphic in the file.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field

from world.state import GHOST_BODIES  # ClassicUO Mobile.IsDead

HERE = os.path.dirname(os.path.abspath(__file__))
WOODS_JSON = os.path.join(HERE, "data", "woods.json")

LAYER_BACKPACK = 0x15            # ClassicUO Game/Data/Layers.cs:28
LOG_GRAPHICS = tuple(range(0x1BDD, 0x1BE3))     # loop_lumber.LOGS
BOARD_GRAPHICS = (0x1BD7,)                       # loop_lumber.BOARDS
CORPSE_GRAPHIC = 0x2006
MAX_DEPTH = 8                    # nested-container recursion bound


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def _int(v):
    if v is None:
        return None
    return int(v, 0) if isinstance(v, str) else int(v)


# ---------------------------------------------------------------- woods
@dataclass
class Woods:
    entries: list = field(default_factory=list)   # [{name, log_graphics:set, log_hue, board_hue, value_gp}]
    source: str | None = None
    log_graphics: frozenset = frozenset(LOG_GRAPHICS)
    board_graphics: frozenset = frozenset(BOARD_GRAPHICS)

    def value_gp(self, name):
        for e in self.entries:
            if e["name"] == name:
                return e.get("value_gp")
        return None


def load_woods(path: str | None = WOODS_JSON) -> Woods:
    """Parse woods.json. A missing, unreadable or malformed file gives an
    empty table; malformed entries are skipped."""
    if not path or not os.path.exists(path):
        return Woods()
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return Woods()
    entries, logs = [], set(LOG_GRAPHICS)
    for w in ((doc.get("woods") or ()) if isinstance(doc, dict) else ()):
        if not isinstance(w, dict) or not isinstance(w.get("name"), str):
            continue
        try:
            graphics = {_int(g) for g in w.get("log_graphics") or ()}
            e = {"name": w["name"], "log_graphics": graphics,
                 "log_hue": _int(w.get("log_hue")), "board_hue": _int(w.get("board_hue")),
                 "value_gp": w.get("value_gp")}
        except (TypeError, ValueError):
            continue
        logs |= graphics
        entries.append(e)
    return Woods(entries=entries, source=doc.get("source") if isinstance(doc, dict) else None,
                 log_graphics=frozenset(logs))


_DEFAULT_WOODS: Woods | None = None


def default_woods() -> Woods:
    global _DEFAULT_WOODS
    if _DEFAULT_WOODS is None:
        _DEFAULT_WOODS = load_woods()
    return _DEFAULT_WOODS


def classify(item: dict, woods: Woods) -> tuple[str, str] | None:
    """("log" | "board", wood_name) for a log/board item dict, else None."""
    g, hue = _int(item.get("graphic")), _int(item.get("hue")) or 0
    if g in woods.board_graphics:
        for e in woods.entries:
            if e["board_hue"] == hue:
                return "board", e["name"]
        return "board", f"unknown({hue})"
    if g in woods.log_graphics:
        match = [e for e in woods.entries if e["log_hue"] == hue]
        exact = [e for e in match if g in e["log_graphics"]]
        if exact or match:
            return "log", (exact or match)[0]["name"]
        return "log", f"unknown({hue})"
    return None


def woods_summary(items, *, kind: str | None = None, woods: Woods | None = None) -> dict:
    """{wood_name: count} over logs and boards (kind "log"/"board" filters).
    `items`: a {serial: item} mapping (world.items or Ledger.items) or an
    iterable of item dicts with graphic/hue/amount."""
    woods = woods or default_woods()
    seq = items.values() if isinstance(items, dict) else items
    out: dict[str, int] = {}
    for it in seq:
        c = classify(it, woods)
        if c is None or (kind is not None and c[0] != kind):
            continue
        out[c[1]] = out.get(c[1], 0) + (it.get("amount") or 1)
    return out


# ---------------------------------------------------------------- pack view
def self_serial(state):
    s = (state.get("movement") or {}).get("self_serial")
    if s is None:
        s = ((state.get("world") or {}).get("self") or {}).get("serial")
    return None if s is None else _serial(s)


def backpack_serial(state) -> int | None:
    me = self_serial(state)
    if me is None:
        return None
    for key, it in ((state.get("world") or {}).get("items") or {}).items():
        if it.get("layer") == LAYER_BACKPACK and it.get("container") is not None \
                and _serial(it["container"]) == me:
            return _serial(key)
    return None


def pack_items(state, pack: int) -> dict[int, dict]:
    """{serial: {graphic, hue, amount, container}} for everything inside
    `pack`, nested containers included."""
    items = (state.get("world") or {}).get("items") or {}
    children: dict[int, list] = {}
    for key, it in items.items():
        if it.get("container") is not None:
            children.setdefault(_serial(it["container"]), []).append((_serial(key), it))
    out, frontier = {}, [pack]
    for _ in range(MAX_DEPTH):
        nxt = []
        for parent in frontier:
            for s, it in children.get(parent, ()):
                if s in out:
                    continue
                out[s] = {"graphic": it.get("graphic"), "hue": it.get("hue") or 0,
                          "amount": it.get("amount") or 1, "container": parent}
                nxt.append(s)
        frontier = nxt
        if not frontier:
            break
    return out


def death_signal(state) -> str | None:
    me = (state.get("world") or {}).get("self") or {}
    body = (me.get("stats") or {}).get("graphic")
    if body in GHOST_BODIES:
        return f"ghost body 0x{body:X}"
    if me.get("hits") == 0 and me.get("hits_max"):
        return "hits 0"
    return None


# ---------------------------------------------------------------- ledger
@dataclass
class Delta:
    t: float
    pack: int | None
    first: bool = False           # baseline observation, nothing compared
    alive: bool = True
    death: bool = False           # death detected in this observation
    death_reason: str | None = None
    gained: list = field(default_factory=list)
    lost: list = field(default_factory=list)
    unexplained_losses: list = field(default_factory=list)

    @property
    def theft_suspected(self) -> bool:
        return bool(self.unexplained_losses)

    @property
    def changed(self) -> bool:
        return bool(self.gained or self.lost or self.death)

    def to_dict(self):
        d = asdict(self)
        d["theft_suspected"] = self.theft_suspected
        return d


class Ledger:
    def __init__(self, woods: Woods | str | None = None, *, expect_ttl_s: float = 30.0,
                 mass_loss_min: int = 2, mass_loss_frac: float = 0.8,
                 death_hold_s: float = 60.0, settle_s: float = 1.0):
        self.woods = load_woods(woods) if isinstance(woods, str) else (woods or default_woods())
        self.expect_ttl_s = expect_ttl_s
        self.mass_loss_min = mass_loss_min
        self.mass_loss_frac = mass_loss_frac
        self.death_hold_s = death_hold_s    # a heuristic death holds this long without a ghost body
        self.settle_s = settle_s            # a whole item gone with no cause is booked after this long
        self.items: dict[int, dict] | None = None     # last pack view
        self.pack: int | None = None
        self.dead = False
        self.dead_until: float | None = None          # None = confirmed (ghost body / hits 0)
        self.pending: list[dict] = []                 # open expectations
        self.settling: dict[int, float] = {}          # serial -> first view without it (still in the baseline)

    # ------------------------------------------------------------ expectations
    def expect(self, *entries, now: float | None = None):
        now = time.time() if now is None else now
        for e in entries:
            e = tuple(e)
            if not e or e[0] not in ("moved_out", "consumed", "spent", "moving"):
                raise ValueError(f"unknown expectation {e!r}")
            if e[0] == "moving":
                self.pending.append({"kind": "moving", "key": e[1], "dest": e[2], "left": None, "t": now})
                continue
            left = e[2] if len(e) > 2 and e[2] is not None else None
            self.pending.append({"kind": e[0], "key": e[1], "left": left, "t": now})

    def _cover(self, serial: int, graphic, amount: int, whole: bool) -> int:
        """Units of this loss covered by expectations (consumes them)."""
        covered = 0
        for p in self.pending:
            if covered >= amount:
                break
            if p["kind"] in ("moved_out", "moving"):
                if p["key"] == serial and whole:
                    covered, p["left"] = amount, 0
            elif (p["kind"] == "consumed" and p["key"] == serial) or \
                    (p["kind"] == "spent" and p["key"] == graphic):
                take = amount - covered if p["left"] is None else min(p["left"], amount - covered)
                covered += take
                if p["left"] is not None:
                    p["left"] -= take
        self.pending = [p for p in self.pending if p["left"] is None or p["left"] > 0]
        return covered

    def _entry(self, serial, rec, amount, **extra):
        e = {"serial": serial, "graphic": rec["graphic"], "hue": rec["hue"], "amount": amount}
        c = classify(rec, self.woods)
        if c:
            e["class"], e["wood"] = c
        e.update(extra)
        return e

    # ------------------------------------------------------------ observe
    def observe(self, state: dict, *, expected=(), now: float | None = None) -> Delta:
        now = time.time() if now is None else now
        if expected:
            self.expect(*expected, now=now)
        self.pending = [p for p in self.pending if now - p["t"] <= self.expect_ttl_s]
        pack = backpack_serial(state)
        reason = death_signal(state)
        if self.dead and reason is None and (self.dead_until is None or now > self.dead_until):
            self.dead, self.dead_until = False, None  # resurrected, or the heuristic lapsed
        elif self.dead and reason is not None:
            self.dead_until = None                    # heuristic death now confirmed
        d = Delta(t=now, pack=pack, alive=reason is None and not self.dead)
        if pack is None:                              # can't see the pack: keep the baseline
            if reason and not self.dead:
                self._die(d, reason, now, heuristic=False)
            return d
        cur = pack_items(state, pack)
        if self.items is None or self.pack != pack:
            d.first = self.items is None
            self.items, self.pack, self.settling = cur, pack, {}
            if reason and not self.dead:
                self._die(d, reason, now, heuristic=False)
            return d
        prev = self.items
        world_items = (state.get("world") or {}).get("items") or {}

        # serial-level losses and gains
        losses, gains = [], []
        for s, rec in prev.items():
            now_rec = cur.get(s)
            if now_rec is None:
                w = world_items.get(f"0x{s:08X}")
                where = "gone" if w is None else (
                    "ground" if w.get("container") is None else w["container"])
                losses.append((s, rec, rec["amount"], True, where))
            elif now_rec["amount"] < rec["amount"]:
                losses.append((s, rec, rec["amount"] - now_rec["amount"], False, "split"))
        # contents of a container that left as a whole travel with it: fold them
        # into the container's entry instead of reporting each one
        gone = {s for s, _r, _n, whole, _w in losses if whole}
        nested: dict[int, int] = {}
        for s, rec, *_ in losses:
            if rec["container"] in gone and s in gone:
                top = rec["container"]
                while prev[top]["container"] in gone:
                    top = prev[top]["container"]
                nested[top] = nested.get(top, 0) + 1
        losses = [x for x in losses if not (x[3] and x[1]["container"] in gone)]
        me = self_serial(state)
        equipped = f"0x{me:08X}" if me is not None else None
        for s, rec, n, _whole, where in losses:
            if where == equipped:                    # worn now, e.g. the hatchet
                d.lost.append(self._entry(s, rec, n, cause="equipped", to=where))
        losses = [x for x in losses if x[4] != equipped]
        for s, rec in cur.items():
            old = prev.get(s)
            if old is None:
                gains.append((s, rec, rec["amount"]))
            elif rec["amount"] > old["amount"]:
                gains.append((s, rec, rec["amount"] - old["amount"]))
        d.gained = [self._entry(s, rec, n) for s, rec, n in gains]

        # classify losses: expected first, then merges, then death/unexplained
        gain_pool: dict[tuple, int] = {}
        for _, rec, n in gains:
            k = (rec["graphic"], rec["hue"])
            gain_pool[k] = gain_pool.get(k, 0) + n
        open_losses = []
        for s, rec, n, whole, where in losses:
            covered = self._cover(s, rec["graphic"], n, whole)
            if covered:
                d.lost.append(self._entry(s, rec, covered, cause="expected", to=where))
            if n - covered > 0:
                open_losses.append((s, rec, n - covered, whole, where))
        rest = []
        for s, rec, n, whole, where in open_losses:
            k = (rec["graphic"], rec["hue"])
            m = min(n, gain_pool.get(k, 0))
            if m:
                gain_pool[k] -= m
                d.lost.append(self._entry(s, rec, m, cause="merged", to=where))
            if n - m > 0:
                rest.append((s, rec, n - m, whole, where))

        heuristic = False
        if rest and not reason and not self.dead:
            corpse = next((w for *_, w in rest if isinstance(w, str) and w.startswith("0x")
                           and (world_items.get(w) or {}).get("graphic") == CORPSE_GRAPHIC), None)
            # a mass vanish is one view's: items already settling were counted when they left
            gone_whole = sum(1 for s, *_, whole, _w in rest if whole and s not in self.settling)
            top = sum(1 for s, r in prev.items() if r["container"] == pack and s not in self.settling)
            if corpse:
                reason = f"pack items moved to corpse {corpse}"
            elif gone_whole >= self.mass_loss_min and \
                    gone_whole >= self.mass_loss_frac * max(top, 1):
                reason = f"mass vanish: {gone_whole} of {top} pack items"
            heuristic = reason is not None
        if reason and not self.dead:
            self._die(d, reason, now, heuristic=heuristic)
        d.alive = not self.dead
        held = {}
        for s, rec, n, whole, where in rest:
            if self.dead:
                d.lost.append(self._entry(s, rec, n, cause="death", to=where))
                continue
            since = self.settling.get(s, now)
            if whole and now - since < self.settle_s:  # may still turn up on us or back in the pack
                held[s] = since
                continue
            e = self._entry(s, rec, n, cause="unexplained", to=where)
            d.lost.append(e)
            d.unexplained_losses.append(e)
        for e in d.lost:
            if e["serial"] in nested:
                e["contents"] = nested[e["serial"]]
        # a settling item (with what it holds) stays in the baseline for the next view to judge
        keep = {}
        for s in gone:
            top = s
            while top not in held and prev[top]["container"] in gone:
                top = prev[top]["container"]
            if top in held:
                keep[s] = prev[s]
        self.items = {**keep, **cur}
        self.settling = held
        # a drag within the pack is over once its stack shows in the destination
        self.pending = [p for p in self.pending if not (
            p["kind"] == "moving" and (cur.get(p["key"]) or {}).get("container") == p["dest"])]
        return d

    def _die(self, d: Delta, reason: str, now: float, *, heuristic: bool):
        self.dead = True
        self.dead_until = now + self.death_hold_s if heuristic else None
        d.death, d.death_reason, d.alive = True, reason, False

    def summary(self, kind: str | None = None) -> dict:
        """woods_summary over the current pack view."""
        return woods_summary(self.items or {}, kind=kind, woods=self.woods)

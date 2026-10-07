"""World state store: last-write-wins snapshots with lazy identity resolution.

Serials are the primary key everywhere; names merge in later (0xFF sub-0x15
name responses) regardless of arrival order — a name landing before the
entity's first sighting is remembered and applied when the entity appears.

Everything in the snapshot is plain JSON-serializable data (dicts of
dataclass-free primitives produced by to_dict()); serials are rendered as
0x-prefixed hex strings for readability.
"""
from dataclasses import dataclass, field


def _h(serial):
    return f"0x{serial:08X}"


# Ghost bodies: ClassicUO Mobile.IsDead (Game/GameObjects/Mobile.cs:132-140). On
# Outlands only S2C 0x20 carries a body graphic (0x77 has none; layouts.py).
GHOST_BODIES = frozenset([0x192, 0x193, 0x25F, 0x260, 0x2B6, 0x2B7])

# The client's view range until the server sets one with S2C 0xC8 (ClassicUO
# World.ClientViewRange = Constants.MAX_VIEW_RANGE). Outlands sends `c8 12` (18) at login.
DEFAULT_VIEW_RANGE = 24
# Pruned mobiles kept in `last_seen` (oldest dropped first)
LAST_SEEN_MAX = 500
# Closed gumps kept in `gumps` besides the open ones (the oldest-opened dropped first).
# Keeping them all made every snapshot grow for the whole session: a day of lumber
# closed ~900 captcha gumps, 1.7 MB of each state-port response (docs/NOTES.md "Viz lag").
CLOSED_GUMPS_MAX = 20
LAYER_MOUNT = 0x19   # equipment layer of the item that stands for the ridden mount


def multi_reach(multi_id: int) -> int:
    """A house's reach from its tile (uomap.multi_reach, the client's multi.mul)."""
    import uomap
    return uomap.multi_reach(multi_id)


@dataclass
class SelfState:
    serial: int | None = None
    name: str | None = None
    account: str | None = None
    # absolute from 0x1B LoginConfirm / 0x20 / 0x77 / 0x21 about self, then
    # advanced by server-confirmed walks (0x22; a new direction only turns);
    # starts relative at (0, 0)
    x: int = 0
    y: int = 0
    z: int = 0
    direction: int = 0
    position_absolute: bool = False
    position_changes: int = 0
    walk_seq: int | None = None
    hits: int | None = None
    hits_max: int | None = None
    mana: int | None = None
    mana_max: int | None = None
    stam: int | None = None
    stam_max: int | None = None
    gold: int | None = None
    weight: int | None = None
    warmode: bool = False
    notoriety: int | None = None
    poisoned: bool | None = None     # 0x16/0x17 type 1 about self (None until the server says)
    map: int | None = None           # facet index from S2C 0xBF sub 8 (0 = map0.uoo)
    faction: str | None = None       # our faction / guild tag from our own click echo (runtime.title_tags)
    guild: str | None = None
    stats: dict = field(default_factory=dict)
    skills: dict = field(default_factory=dict)
    skill_names: list = field(default_factory=list)

    @property
    def body(self) -> int | None:
        return self.stats.get("graphic")

    @property
    def dead(self) -> bool:
        """The client's own rule: the body is a ghost."""
        return self.body in GHOST_BODIES

    def to_dict(self):
        return {k: v for k, v in {
            "serial": _h(self.serial) if self.serial is not None else None,
            "name": self.name, "account": self.account,
            "x": self.x, "y": self.y, "z": self.z,
            "direction": self.direction,
            "position_absolute": self.position_absolute,
            "position_changes": self.position_changes,
            "hits": self.hits, "hits_max": self.hits_max,
            "mana": self.mana, "mana_max": self.mana_max,
            "stam": self.stam, "stam_max": self.stam_max,
            "gold": self.gold, "weight": self.weight,
            "warmode": self.warmode, "notoriety": self.notoriety, "map": self.map,
            "poisoned": self.poisoned,
            "faction": self.faction, "guild": self.guild,
            "body": self.body, "dead": self.dead,
            "stats": self.stats,
            "skills": {str(k): v for k, v in sorted(self.skills.items())},
            "skill_names": self.skill_names,
        }.items()}


@dataclass
class Mobile:
    serial: int
    name: str | None = None
    graphic: int | None = None
    hue: int | None = None
    hits: int | None = None
    hits_max: int | None = None
    mana: int | None = None
    mana_max: int | None = None
    stam: int | None = None
    stam_max: int | None = None
    notoriety: int | None = None
    poisoned: bool | None = None
    flags: int | None = None
    x: int | None = None
    y: int | None = None
    z: int | None = None
    direction: int | None = None
    seen_t: float | None = None    # time of the last S2C packet that updated it
    # "tame" / "bonded" / "summoned": the server's "(tame)" etc. line under a pet's
    # click label (S2C 0x1C type 0, hue 946), sent each time the client asks on sight
    pet: str | None = None
    # a bonded pet's ghost (S2C 0xBF sub 0x19 dead flag); cleared when the server re-sends it alive
    # (it deletes and re-adds the mobile, 0x1D + 0x20, without the flag: live 2026-10-05)
    dead: bool | None = None
    # an Outlands faction member's tag ("[Cambria]") and the guild tag ("[Officer, LoK]") from the
    # title lines under its click label (runtime.title_tags), sent each time the client asks on sight
    faction: str | None = None
    guild: str | None = None

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items() if v is not None
                and k != "serial"}


@dataclass
class Item:
    serial: int
    name: str | None = None
    graphic: int | None = None
    amount: int | None = None
    x: int | None = None
    y: int | None = None
    z: int | None = None
    dir: int | None = None
    hue: int | None = None
    flags: int | None = None
    container: int | None = None   # parent serial (None = ground)
    layer: int | None = None
    grid: int | None = None
    data_type: int | None = None
    v11: int | None = None
    v12: int | None = None
    notoriety: int | None = None   # a corpse's: the latest 0xFF sub 0xDEAD (1 = blue, someone else's kill)

    def to_dict(self):
        d = {k: v for k, v in self.__dict__.items() if v is not None
             and k not in ("serial", "container")}
        if self.container is not None:
            d["container"] = _h(self.container)
        return d


@dataclass
class GumpState:
    serial: int
    gump_id: int
    x: int = 0
    y: int = 0
    layout: str = ""
    lines: list = field(default_factory=list)
    open: bool = False
    compressed: bool = False
    responses: int = 0

    def to_dict(self):
        return {"serial": _h(self.serial), "gump_id": _h(self.gump_id),
                "x": self.x, "y": self.y, "layout": self.layout,
                "lines": self.lines, "open": self.open,
                "compressed": self.compressed, "responses": self.responses}


@dataclass
class TargetState:
    active: bool = False
    target_type: int | None = None
    cursor_id: int | None = None
    cursor_type: int | None = None

    def to_dict(self):
        return {"active": self.active, "target_type": self.target_type,
                "cursor_id": self.cursor_id, "cursor_type": self.cursor_type}


TRACKING_HITS_MAX = 20


@dataclass
class TrackingState:
    """The Tracking skill as the server reports it (live 20261001_214649, docs/NOTES.md
    "Tracking"): "You will now hunt <mode>." (system) sets the hunting mode, "You begin
    hunting." / "You stop hunting." (spoken by the player itself) switch Hunting on and
    off, and every hit is a 0xFF sub 0x1A arrow {serial, x, y, z, "[Hunting] <name>"}.
    The arrow carries no notoriety: `mode` at the time of the hit is the class. A hit can
    be a mobile the server never sent us (beyond the 18-tile view). The arrow is a
    snapshot: it only moves with the next hit."""
    hunting: bool = False
    mode: str | None = None              # e.g. "murderer players", "aggressive creatures"
    arrow: dict | None = None            # the arrow that is up now
    hits: list = field(default_factory=list)   # recent arrow sets, newest last
    seq: int = 0                         # hits seen so far (each hit's `seq`)

    def on_text(self, serial, text, self_serial):
        if serial == 0xFFFFFFFF and text.startswith("You will now hunt ") and text.endswith("."):
            self.mode = text[len("You will now hunt "):-1]
        elif serial is not None and serial == self_serial:   # nobody else can speak as us
            if text == "You begin hunting.":
                self.hunting = True
            elif text == "You stop hunting.":
                self.hunting = False

    def on_arrow_set(self, f):
        self.seq += 1
        hit = {k: f[k] for k in ("arrow_id", "serial", "x", "y", "z", "text")}
        hit.update(mode=self.mode if self.hunting else None, seq=self.seq)
        self.arrow = hit
        self.hits = (self.hits + [hit])[-TRACKING_HITS_MAX:]

    def on_arrow_cancel(self, arrow_id=None):
        if self.arrow is not None and (arrow_id is None or self.arrow["arrow_id"] == arrow_id):
            self.arrow = None

    def to_dict(self):
        def h(hit):
            return {**hit, "serial": _h(hit["serial"])}
        return {"hunting": self.hunting, "mode": self.mode, "seq": self.seq,
                "arrow": h(self.arrow) if self.arrow else None, "hits": [h(x) for x in self.hits]}

class EntityCensus:
    """Serials the client itself asked about (0x09/0x34/0x98 + dialect sub 9).

    A query marks the entity salient to the player; counts approximate how
    often the client re-requested details."""

    def __init__(self):
        self.serials: dict[int, dict] = {}

    def add(self, serial, source):
        e = self.serials.setdefault(serial, {"queries": 0, "sources": set()})
        e["queries"] += 1
        e["sources"].add(source)

    def to_dict(self):
        return {_h(s): {"queries": e["queries"],
                        "sources": sorted(e["sources"])}
                for s, e in sorted(self.serials.items())}


class StateStore:
    """Last-write-wins entity snapshots plus the lazy name map."""

    def __init__(self):
        self.self = SelfState()
        self.mobiles: dict[int, Mobile] = {}
        self.items: dict[int, Item] = {}
        self.gumps: dict[tuple[int, int], GumpState] = {}
        self.target = TargetState()
        self.tracking = TrackingState()
        self.census = EntityCensus()
        self.names: dict[int, str] = {}
        # latest click label per serial (S2C 0x1C type 6, e.g. "Len the banker")
        self.labels: dict[int, str] = {}
        self.buffs: dict[int, dict[int, dict]] = {}  # serial -> icon id -> info
        # latest S2C 0xFF sub 3 TimeSync: the server's ms clock ("ms") at our time "t"
        self.server_time: dict | None = None
        self.containers: set[int] = set()
        self.protocol_version: int | None = None
        self.characters: list[str] = []  # 0xA9 character-list slot names
        # mobiles with an outstanding client status request (0x34 type 4 since the last
        # close-status bf 000c or delete): ClassicUO Entity.HitsRequest >= Pending
        self.status_requested: set[int] = set()
        # Chebyshev radius beyond which the client drops mobiles and ground items
        # (S2C 0xC8; World.ProcessDeletes, docs/WORLDMODEL.md "Pruning")
        self.view_range = DEFAULT_VIEW_RANGE
        # mobiles pruned from `mobiles`: serial -> {**to_dict(), t, facet, why}; for
        # "seen earlier" displays only, never for deciding what the client can act on
        self.last_seen: dict[int, dict] = {}
        # latest S2C 0x2F per attacker: serial -> {"defender": serial, "t": float}
        self.swings: dict[int, dict] = {}
        # time of the packet being applied (WorldRuntime sets it); stamps Mobile.seen_t
        self.now: float | None = None
        # the layer each item was last worn on by self (S2C 0x2E / 0x78): kept after it
        # leaves the paperdoll (a lift deletes it, 0x25 re-adds it without a layer), the
        # way Razor's dress list remembers it; `ctl act equip` falls back to it
        self.worn_layers: dict[int, int] = {}

    # -- lazy name merge (both orders) -------------------------------------
    def apply_names(self, entries):
        for e in entries:
            serial, name = e["serial"], e["name"]
            self.names[serial] = name
            if serial in self.items:
                self.items[serial].name = name
            if serial in self.mobiles:
                self.mobiles[serial].name = name
                self.mobiles[serial].seen_t = self.now
            if self.self.serial == serial:
                self.self.name = name

    # -- entity upserts ------------------------------------------------------
    def upsert_item(self, serial, **fields):
        it = self.items.get(serial)
        if it is None:
            it = Item(serial, name=self.names.get(serial))
            self.items[serial] = it
        for k, v in fields.items():
            if hasattr(it, k):
                setattr(it, k, v)
        return it

    def note_worn(self, serial, layer, parent):
        if layer and parent is not None and parent == self.self.serial:
            self.worn_layers[serial] = layer

    def upsert_mobile(self, serial, **fields):
        m = self.mobiles.get(serial)
        if m is None:
            m = Mobile(serial, name=self.names.get(serial))
            self.mobiles[serial] = m
            self.last_seen.pop(serial, None)
        for k, v in fields.items():
            if hasattr(m, k):
                setattr(m, k, v)
        m.seen_t = self.now
        return m

    def update_mobile(self, serial, **fields):
        """Like upsert_mobile, but only for a mobile the client has: the stock
        handlers of packets without a position (0x11, 0x2D, 0xA1-0xA3, 0x16/0x17,
        0x2E's parent) look the serial up and ignore unknown ones (ClassicUO
        World.Get / Mobiles.Get), so they must not bring a removed mobile back."""
        return self.upsert_mobile(serial, **fields) if serial in self.mobiles else None

    # -- removal ---------------------------------------------------------------
    def _drop_items(self, roots):
        """Remove the items under `roots` (any serials) recursively, like
        ClassicUO RemoveItem/RemoveMobile remove their children."""
        frontier = set(roots)
        while frontier:
            kids = [s for s, it in self.items.items() if it.container in frontier]
            for s in kids:
                del self.items[s]
                self.containers.discard(s)
            frontier = set(kids)

    def remove_item(self, serial):
        it = self.items.pop(serial, None)
        if it is not None:
            self.containers.discard(serial)
            self._drop_items([serial])
        return it

    def open_gump(self, g):
        """Store a gump the server opened; a reopened (serial, gump_id) moves to the
        newest end, so the closed ones forget_closed_gumps drops are the oldest-opened."""
        key = (g.serial, g.gump_id)
        self.gumps.pop(key, None)
        self.gumps[key] = g

    def forget_closed_gumps(self):
        """Keep every open gump and the CLOSED_GUMPS_MAX newest closed ones."""
        closed = [k for k, g in self.gumps.items() if not g.open]
        for k in closed[:max(len(closed) - CLOSED_GUMPS_MAX, 0)]:
            del self.gumps[k]

    def _pop_mobile(self, serial, why, facet):
        m = self.mobiles.pop(serial, None)
        if m is None:
            return None
        self.containers.discard(serial)
        self.status_requested.discard(serial)
        self.swings.pop(serial, None)
        facet = self.self.map if facet is None else facet
        self.last_seen[serial] = {**m.to_dict(), "t": self.now, "facet": facet, "why": why}
        if len(self.last_seen) > LAST_SEEN_MAX:
            oldest = min(self.last_seen, key=lambda s: self.last_seen[s]["t"] or 0)
            del self.last_seen[oldest]
        return m

    def remove_mobile(self, serial, why="delete", facet=None):
        """Remove a mobile with its equipment and record it in last_seen with
        `why`: "range", "facet", "dead" or "delete" (S2C 0x1D)."""
        m = self._pop_mobile(serial, why, facet)
        if m is not None:
            self._drop_items([serial])
        return m

    def delete(self, serial):
        """S2C 0x1D: an item or a mobile, with everything under it."""
        return self.remove_item(serial) or self.remove_mobile(serial)

    def mounted(self) -> bool:
        """The player rides: an item on the mount layer (0x19) is equipped on
        self, as the server sent it (0x2E/0x78; a dismount deletes it with 0x1D).
        The stock client decides its step speed the same way (FindItemByLayer(Mount))."""
        me = self.self.serial
        return me is not None and any(it.layer == LAYER_MOUNT and it.container == me
                                      for it in self.items.values())

    def root_of(self, serial):
        """Outermost container of an item (the item itself if on the ground or
        its container is unknown)."""
        seen = set()
        while serial in self.items and self.items[serial].container is not None and serial not in seen:
            seen.add(serial)
            serial = self.items[serial].container
        return serial

    def _remove_many(self, mobiles, items, why, facet):
        for s in mobiles:
            self._pop_mobile(s, why, facet)
        for s in items:
            del self.items[s]
            self.containers.discard(s)
        if mobiles or items:
            self._drop_items([*mobiles, *items])

    def prune_range(self, x, y, facet=None):
        """World.ProcessDeletes: drop mobiles and ground items farther than
        view_range (Chebyshev) from (x, y), with everything under them. A house
        (data_type 2 multi) stays while within view_range + its reach (ClassicUO
        HouseManager.IsHouseInRange, Item.MultiDistanceBonus): the server sends houses
        from farther out (session 20261002_153718: the Corpse Creek house at 22 tiles,
        three times; dropped each time, it was missing at all 12 denies it caused).
        Positionless mobiles (0x78 before their first 0x20) stay. Returns the removed
        mobile serials."""
        r = self.view_range

        def far(e, extra=0):
            return e.x is not None and e.y is not None and max(abs(e.x - x), abs(e.y - y)) > r + extra
        gone = [s for s, m in self.mobiles.items() if s != self.self.serial and far(m)]
        ground = [s for s, it in self.items.items() if it.container is None
                  and far(it, multi_reach(it.graphic) if it.data_type == 2 and it.graphic is not None else 0)]
        self._remove_many(gone, ground, "range", facet)
        return gone

    def clear_facet(self, facet=None):
        """Facet change (ClassicUO InternalMapChangeClear(noplayer: true)):
        every mobile but self, every item not carried by self. Returns the
        removed mobile serials."""
        me = self.self.serial
        gone = [s for s in self.mobiles if s != me]
        items = [s for s in self.items if me is None or self.root_of(s) != me]
        self._remove_many(gone, items, "facet", facet)
        return gone

    def buff_ends_t(self, buff: dict) -> float | None:
        """Our wall time when the buff runs out: the latest timer `end` (server ms,
        0 = never, as the client's OutlandsBuffUpdate takes it) mapped through the
        last TimeSync. None without an end or before the first sync. The buff stays
        in `buffs` after that time until the server removes it (sub 9): the stock
        client drops a buff only on the server's word (ClassicUO PlayerMobile.RemoveBuff
        is called from the packet handler alone; BuffGump only re-lays out a timed-out
        icon), and the server has left ended buffs unremoved (capture 20261003_111419)."""
        ends = [t.get("end") for t in buff.get("timers") or []]
        st = self.server_time
        if not ends or not all(ends) or st is None or st.get("t") is None:
            return None
        return round(st["t"] + (max(ends) - st["ms"]) / 1000, 3)

    # -- snapshot --------------------------------------------------------------
    def snapshot(self):
        return {
            "self": self.self.to_dict(),
            "protocol_version": self.protocol_version,
            "characters": self.characters,
            "mobiles": {_h(s): m.to_dict()
                        for s, m in sorted(self.mobiles.items())},
            "last_seen": {_h(s): e for s, e in sorted(self.last_seen.items())},
            "swings": {_h(s): {"defender": _h(w["defender"]), "t": w["t"]}
                       for s, w in sorted(self.swings.items())},
            "view_range": self.view_range,
            "items": {_h(s): it.to_dict()
                      for s, it in sorted(self.items.items())},
            "gumps": [g.to_dict() for _, g in sorted(self.gumps.items())],
            "target": self.target.to_dict(),
            "tracking": self.tracking.to_dict(),
            "census": self.census.to_dict(),
            "names": {_h(s): n for s, n in sorted(self.names.items())},
            "labels": {_h(s): t for s, t in sorted(self.labels.items())},
            "buffs": {_h(s): {str(i): {**b, "ends_t": self.buff_ends_t(b)} for i, b in sorted(v.items())}
                      for s, v in sorted(self.buffs.items())},
            "server_time": self.server_time,
            "containers": [_h(s) for s in sorted(self.containers)],
            "status_requested": [_h(s) for s in sorted(self.status_requested)],
            "worn_layers": {_h(s): layer for s, layer in sorted(self.worn_layers.items())},
        }

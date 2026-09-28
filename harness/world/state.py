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


@dataclass
class SelfState:
    serial: int | None = None
    name: str | None = None
    account: str | None = None
    # dead-reckoned from C2S walks until an absolute server position arrives
    # (0x20 UpdatePlayer / 0x21 DenyWalk); starts relative at (0, 0)
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
    stats: dict = field(default_factory=dict)
    skills: dict = field(default_factory=dict)
    skill_names: list = field(default_factory=list)

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
            "warmode": self.warmode, "notoriety": self.notoriety,
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
        self.census = EntityCensus()
        self.names: dict[int, str] = {}
        self.buffs: dict[int, dict[int, dict]] = {}  # serial -> icon id -> info
        self.containers: set[int] = set()
        self.protocol_version: int | None = None

    # -- lazy name merge (both orders) -------------------------------------
    def apply_names(self, entries):
        for e in entries:
            serial, name = e["serial"], e["name"]
            self.names[serial] = name
            if serial in self.items:
                self.items[serial].name = name
            if serial in self.mobiles:
                self.mobiles[serial].name = name
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

    def upsert_mobile(self, serial, **fields):
        m = self.mobiles.get(serial)
        if m is None:
            m = Mobile(serial, name=self.names.get(serial))
            self.mobiles[serial] = m
        for k, v in fields.items():
            if hasattr(m, k):
                setattr(m, k, v)
        return m

    def delete(self, serial):
        return self.items.pop(serial, None) or self.mobiles.pop(serial, None)

    # -- snapshot --------------------------------------------------------------
    def snapshot(self):
        return {
            "self": self.self.to_dict(),
            "protocol_version": self.protocol_version,
            "mobiles": {_h(s): m.to_dict()
                        for s, m in sorted(self.mobiles.items())},
            "items": {_h(s): it.to_dict()
                      for s, it in sorted(self.items.items())},
            "gumps": [g.to_dict() for _, g in sorted(self.gumps.items())],
            "target": self.target.to_dict(),
            "census": self.census.to_dict(),
            "names": {_h(s): n for s, n in sorted(self.names.items())},
            "buffs": {_h(s): {str(i): b for i, b in sorted(v.items())}
                      for s, v in sorted(self.buffs.items())},
            "containers": [_h(s) for s in sorted(self.containers)],
        }

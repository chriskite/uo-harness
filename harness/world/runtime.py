"""WorldRuntime: dispatch framed packets into the StateStore + event queue.

feed_packet(direction, payload) is the single entry point. Packets with no
registered handler are counted in `unhandled` (visibility into coverage gaps)
and otherwise ignored; truncated/corrupt packets increment `parse_failures`
and are dropped — neither is ever fatal.

C2S is a first-class input: walks dead-reckon the player's position, entity
queries feed the census (salience), and the login-time self-status query
establishes the player's serial (see _adopt_self_serial).

Identity guards: 0x20 UpdatePlayer and 0x1B LoginConfirm only ever carry the
player serial, so a mismatch against the known self serial means the packet
was mis-framed out of the 0x00-family world-data stream (docs/WORLDMODEL.md
§6/Open Question #1 — naive top-level framing chops that record stream and
record content can surface under covered ids). Mismatches are counted in
`anomalies` and ignored, mirroring the client's own no-op behavior.
"""
import collections

from . import parsers
from .state import StateStore, GumpState

# direction constants
C2S = "c2s"
S2C = "s2c"

# doc §6: the 0x00-family carries the (unparsed) world-data record stream.
# Registered as known-but-unparsed so they don't count as unhandled ids.
WORLD_DATA_S2C = {0x00, 0x3F, 0x40, 0x52}

# UO direction deltas: 0=N 1=NE 2=E 3=SE 4=S 5=SW 6=W 7=NW
_DELTAS = ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1))


class WorldRuntime:
    def __init__(self):
        self.state = StateStore()
        self.events: list[dict] = []
        self.packet_counts = collections.Counter()   # (direction, pid) -> n
        self.unhandled = collections.Counter()       # ids with no handler
        self.dialect_unhandled = collections.Counter()  # (direction, subId)
        self.parse_failures = 0
        self.anomalies = collections.Counter()

    def drain_events(self):
        out, self.events = self.events, []
        return out

    def _emit(self, ev, **fields):
        self.events.append({"ev": ev, **fields})

    def _adopt_self_serial(self, serial):
        """The client's login burst queries its own serial before anything
        else (both captures: 09/34/98 on 0x00094375 straight after char
        select; the same serial appears as the clicked serial in the C2S
        target response). The first queried serial is therefore self."""
        if self.state.self.serial is None:
            self.state.self.serial = serial

    # ------------------------------------------------------------------
    def feed_packet(self, direction, payload):
        if not payload:
            self.parse_failures += 1
            return
        pid = payload[0]
        self.packet_counts[(direction, pid)] += 1
        if direction == S2C and pid in WORLD_DATA_S2C:
            return  # known world-data carrier, grammar open (doc §6)
        handler = (_C2S_HANDLERS if direction == C2S
                   else _S2C_HANDLERS).get(pid)
        if handler is None:
            self.unhandled[(direction, pid)] += 1
            return
        try:
            fields = parsers.parse_packet(direction, payload)
        except parsers.PacketIncomplete:
            self.parse_failures += 1
            return
        if fields is None:  # no parser for a registered id — defensive
            self.parse_failures += 1
            return
        handler(self, fields)


# ---------------------------------------------------------------------------
# S2C handlers
# ---------------------------------------------------------------------------

def _h_damage(rt, f):
    rt._emit("damage", serial=f["serial"], amount=f["amount"])


def _h_confirm_walk(rt, f):
    noto = f["notoriety"] & 0xBF
    if noto == 0 or noto > 7:
        noto = 1
    rt.state.self.notoriety = noto
    rt._emit("walk_confirm", seq=f["seq"], notoriety=noto)


def _h_deny_walk(rt, f):
    s = rt.state.self
    s.x, s.y, s.z = f["x"], f["y"], f["z"]
    s.direction = f["dir"]
    s.position_absolute = True
    rt._emit("walk_deny", seq=f["seq"], x=s.x, y=s.y, z=s.z,
             direction=s.direction)


def _route_vitals(rt, serial, **vitals):
    if rt.state.self.serial == serial:
        for k, v in vitals.items():
            setattr(rt.state.self, k, v)
    else:
        rt.state.upsert_mobile(serial, **vitals)


def _h_mobile_attributes(rt, f):
    _route_vitals(rt, f["serial"], hits_max=f["hits_max"], hits=f["hits"],
                  mana_max=f["mana_max"], mana=f["mana"],
                  stam_max=f["stam_max"], stam=f["stam"])


def _h_swing(rt, f):
    rt._emit("swing", attacker=f["attacker"], defender=f["defender"])


def _stat_handler(cur_attr, max_attr):
    def h(rt, f):
        _route_vitals(rt, f["serial"],
                      **{cur_attr: f["current"], max_attr: f["max"]})
    return h


def _h_warmode(rt, f):
    rt.state.self.warmode = f["flag"] != 0


def _h_update_player(rt, f):
    s = rt.state.self
    if s.serial is None:
        s.serial = f["serial"]  # doc §1: only ever carries the player serial
    elif s.serial != f["serial"]:
        rt.anomalies["update_player_mismatch"] += 1
        return
    s.x, s.y, s.z = f["x"], f["y"], f["z"]
    s.direction = f["dir"] & 7
    s.position_absolute = True
    s.stats["graphic"] = f["graphic"]
    s.stats["hue"] = f["hue"]
    s.stats["flags"] = f["flags"]
    s.notoriety = f["notoriety"]


def _h_update_item_sa(rt, f):
    it = rt.state.upsert_item(
        f["serial"], graphic=f["graphic"], amount=f["amount"] or 1,
        x=f["x"], y=f["y"], z=f["z"], dir=f["dir"], hue=f["hue"],
        flags=f["flags"], container=None, data_type=f["data_type"],
        v11=f["v11"])
    rt._emit("item_seen", serial=it.serial, graphic=it.graphic,
             container=None)


def _h_equip_item(rt, f):
    rt.state.upsert_mobile(f["parent"])
    it = rt.state.upsert_item(f["item"], graphic=f["graphic"],
                              layer=f["layer"], container=f["parent"],
                              hue=f["hue"])
    rt._emit("item_seen", serial=it.serial, graphic=it.graphic,
             container=f["parent"])


def _h_delete(rt, f):
    rt.state.delete(f["serial"])
    rt._emit("delete", serial=f["serial"])


def _h_open_container(rt, f):
    rt.state.containers.add(f["serial"])
    rt._emit("container_open", serial=f["serial"], gump_id=f["gump_id"])


def _h_contained_item(rt, f):
    it = rt.state.upsert_item(
        f["serial"], graphic=f["graphic"], amount=max(f["amount"], 1),
        x=f["x"], y=f["y"], grid=f["grid"], container=f["container"],
        hue=f["hue"], v12=f["v12"])
    rt._emit("item_seen", serial=it.serial, graphic=it.graphic,
             container=f["container"])


def _h_container_content(rt, f):
    for rec in f["items"]:
        rt.state.upsert_item(
            rec["serial"], graphic=rec["graphic"], amount=rec["amount"],
            x=rec["x"], y=rec["y"], grid=rec["grid"],
            container=rec["container"], hue=rec["hue"], v11=rec["v11"],
            v12=rec["v12"])
    rt._emit("container_content", count=len(f["items"]))


def _h_target_cursor(rt, f):
    t = rt.state.target
    t.active, t.target_type = True, f["target_type"]
    t.cursor_id, t.cursor_type = f["cursor_id"], f["cursor_type"]
    rt._emit("target", target_type=t.target_type, cursor_id=t.cursor_id,
             cursor_type=t.cursor_type)


def _h_animation(rt, f):
    rt._emit("animation", serial=f["serial"], action=f["action"],
             frames=f["frames"], repeat=f["repeat"],
             backward=f["backward"], repeat_flag=f["repeat_flag"],
             delay=f["delay"])


def _h_login_confirm(rt, f):
    s = rt.state.self
    if s.serial is None:
        s.serial = f["serial"]
    elif s.serial != f["serial"]:
        rt.anomalies["login_confirm_mismatch"] += 1


def _h_character_status(rt, f):
    s = rt.state.self
    if s.serial == f["serial"]:
        if f["name"]:
            s.name = f["name"]
        s.hits, s.hits_max = f["hits"], f["hits_max"]
        for k, v in f.items():
            if k in ("str", "dex", "int", "stam", "stam_max", "mana",
                     "mana_max", "gold", "weight"):
                setattr(s, k, v)
            elif k not in ("serial", "name", "hits", "hits_max", "renamable",
                           "type"):
                s.stats[k] = v
    else:
        rt.state.upsert_mobile(f["serial"], name=f["name"] or None,
                               hits=f["hits"], hits_max=f["hits_max"])


def _h_skills(rt, f):
    s = rt.state.self
    if f["type"] == 0xFE:
        s.skill_names = [n["name"] for n in f["names"]]
    else:
        for sk in f["skills"]:
            s.skills[sk["id"]] = {k: v for k, v in sk.items() if k != "id"}


def _h_world_item(rt, f):
    fields = {k: f[k] for k in ("graphic", "amount", "x", "y", "z", "dir",
                                "hue", "flags") if k in f}
    it = rt.state.upsert_item(f["serial"], container=None, **fields)
    rt._emit("item_seen", serial=it.serial, graphic=it.graphic,
             container=None)


def _h_corpse_equipment(rt, f):
    for e in f["equipment"]:
        rt.state.upsert_item(e["serial"], container=f["corpse"],
                             layer=e["layer"])


def _h_healthbar(rt, f):
    m = rt.state.upsert_mobile(f["serial"])
    for e in f["entries"]:
        if e["type"] == 1:  # hits-bar poisoned flag (doc §2)
            m.poisoned = bool(e["enabled"])


def _gump_open(rt, f):
    g = GumpState(serial=f["serial"], gump_id=f["gump_id"], x=f["x"],
                  y=f["y"], layout=f["layout"], lines=f["lines"], open=True,
                  compressed=bool(f.get("compressed")))
    rt.state.gumps[(g.serial, g.gump_id)] = g
    rt._emit("gump_open", serial=g.serial, gump_id=g.gump_id, x=g.x, y=g.y,
             layout=g.layout, lines=g.lines)


# ---------------------------------------------------------------------------
# 0xFF dialect sub-dispatch
# ---------------------------------------------------------------------------

def _d_s2c(rt, f):
    sub = f["sub"]
    if sub == 0:
        rt.state.protocol_version = f["version"]
        rt._emit("dialect_handshake", version=f["version"],
                 flag1=f["flag1"], flag2=f["flag2"])
    elif sub == 3:
        rt._emit("keepalive", direction=S2C, timestamp=f["timestamp"])
    elif sub == 8:
        buffs = rt.state.buffs.setdefault(f["serial"], {})
        buffs[f["icon_id"]] = {k: v for k, v in f.items()
                               if k not in ("sub", "serial")}
        rt._emit("buff_update", serial=f["serial"], icon_id=f["icon_id"],
                 title=f["title"])
    elif sub == 9:
        rt.state.buffs.get(f["serial"], {}).pop(f["buff_id"], None)
        rt._emit("buff_remove", serial=f["serial"], buff_id=f["buff_id"])
    elif sub == 0x15:
        rt.state.apply_names(f["entries"])
        rt._emit("names", count=len(f["entries"]), entries=f["entries"])
    else:
        rt.dialect_unhandled[(S2C, sub)] += 1


def _d_c2s(rt, f):
    sub = f["sub"]
    if sub == 3:
        rt._emit("keepalive", direction=C2S)
    elif sub == 4:
        rt._emit("spell_cast", spell_id=f["spell_id"], flag=f["flag"])
    elif sub == 9:
        rt.state.census.add(f["serial"], "0xff09")
        rt._emit("item_query", serial=f["serial"])
    else:
        rt.dialect_unhandled[(C2S, sub)] += 1


def _h_dialect_s2c(rt, f):
    _d_s2c(rt, f)


def _h_dialect_c2s(rt, f):
    _d_c2s(rt, f)


# ---------------------------------------------------------------------------
# C2S handlers
# ---------------------------------------------------------------------------

def _h_walk(rt, f):
    s = rt.state.self
    direction = f["dir"] & 7
    dx, dy = _DELTAS[direction]
    s.x += dx
    s.y += dy
    s.direction = direction
    s.walk_seq = f["seq"]
    s.position_changes += 1
    rt._emit("walk", dir=direction, run=bool(f["dir"] & 0x80), seq=f["seq"],
             x=s.x, y=s.y, moved=True)


def _h_dclick(rt, f):
    rt._emit("dclick", serial=f["serial"])


def _query_handler(pid):
    def h(rt, f):
        rt._adopt_self_serial(f["serial"])
        rt.state.census.add(f["serial"], f"0x{pid:02x}")
        rt._emit("query", serial=f["serial"], kind=pid)
    return h


def _h_char_select(rt, f):
    rt.state.self.name = f["name"]
    rt._emit("char_select", name=f["name"])


def _h_login(rt, f):
    rt.state.self.account = f["account"]
    rt._emit("login", account=f["account"])


def _h_speech(rt, f):
    rt._emit("speech", type=f["type"], hue=f["hue"], font=f["font"],
             lang=f["lang"], text=f["text"])


def _h_gump_response(rt, f):
    g = rt.state.gumps.get((f["serial"], f["gump_id"]))
    if g is not None:
        g.responses += 1
    rt._emit("gump_response", serial=f["serial"], gump_id=f["gump_id"],
             button_id=f["button_id"], switches=f["switches"],
             texts=f["texts"])


def _h_target_response(rt, f):
    rt.state.target.active = False
    rt._emit("target_response", serial=f["serial"], cursor_id=f["cursor_id"],
             target_type=f["target_type"], x=f["x"], y=f["y"], z=f["z"],
             graphic=f["graphic"])


_S2C_HANDLERS = {
    0x0B: _h_damage,
    0x22: _h_confirm_walk,
    0x21: _h_deny_walk,
    0x2D: _h_mobile_attributes,
    0x2F: _h_swing,
    0xA1: _stat_handler("hits", "hits_max"),
    0xA2: _stat_handler("mana", "mana_max"),
    0xA3: _stat_handler("stam", "stam_max"),
    0x72: _h_warmode,
    0x20: _h_update_player,
    0xF3: _h_update_item_sa,
    0x2E: _h_equip_item,
    0x1D: _h_delete,
    0x24: _h_open_container,
    0x25: _h_contained_item,
    0x3C: _h_container_content,
    0x6C: _h_target_cursor,
    0x6E: _h_animation,
    0x1B: _h_login_confirm,
    0x11: _h_character_status,
    0x3A: _h_skills,
    0x1A: _h_world_item,
    0x89: _h_corpse_equipment,
    0x16: _h_healthbar,
    0x17: _h_healthbar,
    0xB0: _gump_open,
    0xDD: _gump_open,
    0xFF: _h_dialect_s2c,
}

_C2S_HANDLERS = {
    0x02: _h_walk,
    0x06: _h_dclick,
    0x09: _query_handler(0x09),
    0x34: _query_handler(0x34),
    0x98: _query_handler(0x98),
    0x5D: _h_char_select,
    0x91: _h_login,
    0xAD: _h_speech,
    0xB1: _h_gump_response,
    0x6C: _h_target_response,
    0xFF: _h_dialect_c2s,
}

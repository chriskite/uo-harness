"""WorldRuntime: dispatch framed packets into the StateStore + event queue.

feed_packet(direction, payload) is the single entry point. Packets with no
registered handler are counted in `unhandled` (visibility into coverage gaps)
and otherwise ignored; truncated/corrupt packets increment `parse_failures`
and are dropped — neither is ever fatal.

C2S is a first-class input: walk requests are held as pending and move the
player only when the server confirms them (0x22; a walk in a new direction only
turns), entity queries feed the census (salience), and the login-time
self-status query establishes the player's serial (see _adopt_self_serial).

Identity: 0x1B LoginConfirm names the player serial (the C2S login-burst
query adopts it earlier in replay order; a disagreeing 0x1B is counted in
`anomalies`). 0x20 MobileUpdate and 0x77 MobileMove carry ANY mobile (the
client's handlers look non-self serials up in the mobile table), so they
update self only when the serial matches and otherwise upsert a mobile.

Pruning (docs/WORLDMODEL.md "Pruning"): `state.mobiles` holds only what the
stock client still has. Mobiles leave on 0x1D, on a death (0xFF sub 0xDEAD,
0xAF), beyond the 0xC8 view range from self (World.ProcessDeletes: run on
0xFF sub 5 and whenever self's position changes) and on a facet change;
each lands in `state.last_seen` and emits `prune`. Packets without a
position update only mobiles the model still has.
"""
import collections
import time

from . import parsers
from .state import StateStore, GumpState

# direction constants
C2S = "c2s"
S2C = "s2c"

# UO direction deltas: 0=N 1=NE 2=E 3=SE 4=S 5=SW 6=W 7=NW
_DELTAS = ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1))
PENDING_WALKS_MAX = 64  # unconfirmed walk requests kept for confirm matching
CORPSES_MAX = 1000      # corpse serials remembered for one `mobile_death` per corpse


class WorldRuntime:
    def __init__(self, clock=time.time):
        self.clock = clock               # wall time of the packet being fed (replay: the row's t)
        self.state = StateStore()
        self.events: list[dict] = []
        self.packet_counts = collections.Counter()   # (direction, pid) -> n
        self.unhandled = collections.Counter()       # ids with no handler
        self.dialect_unhandled = collections.Counter()  # (direction, subId)
        self.parse_failures = 0
        self.anomalies = collections.Counter()
        # walk requests awaiting the server's 0x22 confirm: [(seq, direction)]
        self.pending_walks: list[tuple[int, int]] = []
        # corpses already reported by `mobile_death` (0xDEAD repeats per corpse)
        self.corpses: collections.OrderedDict[int, None] = collections.OrderedDict()

    def drain_events(self):
        out, self.events = self.events, []
        return out

    def _emit(self, ev, **fields):
        self.events.append({"ev": ev, **fields})

    def prune(self):
        """World.ProcessDeletes from self's server position (needs both)."""
        s = self.state.self
        if s.serial is None or not s.position_absolute:
            return
        for serial in self.state.prune_range(s.x, s.y, s.map):
            self._emit("prune", serial=serial, why="range")

    def remove_dead(self, serial):
        """A mobile died. The stock 0xAF handler re-keys the dead mobile to
        serial | 0x80000000, so its serial stops resolving: it leaves `mobiles`
        (last_seen why "dead")."""
        if serial == self.state.self.serial:
            return
        if self.state.remove_mobile(serial, "dead", self.state.self.map) is not None:
            self._emit("prune", serial=serial, why="dead")
        elif serial in self.state.last_seen:   # it left the view earlier, then died
            e = self.state.last_seen[serial]
            if e["why"] != "dead":
                e.update(why="dead", dead_t=self.state.now)

    def mobile_death(self, serial, corpse, name):
        """Outlands 0xFF sub 0xDEAD: a corpse's data (owner serial, corpse name).
        It follows 0xAF + 0x1D when a mobile dies in view, and comes alone
        whenever a corpse is (re)sent, e.g. on walking up to a mobile that died
        out of view (live 20261001_214649). The owner is dead either way; one
        `mobile_death` per corpse."""
        if serial == self.state.self.serial:
            return
        self.remove_dead(serial)
        if corpse in self.corpses:
            return
        self.corpses[corpse] = None
        if len(self.corpses) > CORPSES_MAX:
            self.corpses.popitem(last=False)
        self._emit("mobile_death", serial=serial, corpse=corpse, name=name)

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
        if direction == S2C:
            self.state.now = self.clock()
        handler(self, fields)


# ---------------------------------------------------------------------------
# S2C handlers
# ---------------------------------------------------------------------------

def _h_damage(rt, f):
    rt._emit("damage", serial=f["serial"], amount=f["amount"])


def _h_confirm_walk(rt, f):
    """S2C 0x22: the server accepted walk `seq`. Apply it to self: a walk in a
    new direction only turns, otherwise it moves one tile. Pending walks sent
    before the confirmed one got no confirm, so the server rejected them:
    they are dropped without moving self."""
    s = rt.state.self
    noto = f["notoriety"] & 0xBF
    if noto == 0 or noto > 7:
        noto = 1
    s.notoriety = noto
    moved = False
    idx = next((i for i, (seq, _) in enumerate(rt.pending_walks) if seq == f["seq"]), None)
    if idx is not None:
        direction = rt.pending_walks[idx][1]
        del rt.pending_walks[:idx + 1]
        if s.direction != direction:
            s.direction = direction
        else:
            dx, dy = _DELTAS[direction]
            s.x += dx
            s.y += dy
            s.position_changes += 1
            moved = True
    rt._emit("walk_confirm", seq=f["seq"], notoriety=noto, x=s.x, y=s.y,
             direction=s.direction, moved=moved)
    if moved:
        rt.prune()


def _h_deny_walk(rt, f):
    """S2C 0x21: walk rejected; snap to the server position. The server resets
    its walker, so walks still pending will not be confirmed."""
    s = rt.state.self
    s.x, s.y, s.z = f["x"], f["y"], f["z"]
    s.direction = f["dir"] & 7
    s.position_absolute = True
    rt.pending_walks.clear()
    rt._emit("walk_deny", seq=f["seq"], x=s.x, y=s.y, z=s.z,
             direction=s.direction)
    rt.prune()


def _route_vitals(rt, serial, **vitals):
    if rt.state.self.serial == serial:
        for k, v in vitals.items():
            setattr(rt.state.self, k, v)
    else:
        rt.state.update_mobile(serial, **vitals)


def _h_mobile_attributes(rt, f):
    _route_vitals(rt, f["serial"], hits_max=f["hits_max"], hits=f["hits"],
                  mana_max=f["mana_max"], mana=f["mana"],
                  stam_max=f["stam_max"], stam=f["stam"])


def _h_swing(rt, f):
    """0x2F: `attacker` swings at `defender`. The latest swing per attacker the
    client has (or self) is kept in state.swings."""
    a = f["attacker"]
    if a == rt.state.self.serial or rt.state.update_mobile(a) is not None:
        rt.state.swings[a] = {"defender": f["defender"], "t": rt.state.now}
    rt._emit("swing", attacker=a, defender=f["defender"])


def _stat_handler(cur_attr, max_attr):
    def h(rt, f):
        _route_vitals(rt, f["serial"],
                      **{cur_attr: f["current"], max_attr: f["max"]})
    return h


def _h_warmode(rt, f):
    rt.state.self.warmode = f["flag"] != 0


def _set_self_position(s, f):
    s.x, s.y, s.z = f["x"], f["y"], f["z"]
    s.direction = f["dir"] & 7
    s.position_absolute = True


def _h_update_player(rt, f):
    """0x20: self when the serial matches, otherwise a nearby mobile. For self
    it carries the body: a change to or from a ghost body emits `death` /
    `resurrect` (ClassicUO Mobile.IsDead)."""
    s = rt.state.self
    if s.serial == f["serial"]:
        was_dead = s.dead if s.body is not None else None
        _set_self_position(s, f)
        s.stats["graphic"] = f["graphic"]
        s.stats["hue"] = f["hue"]
        s.stats["flags"] = f["flags"]
        s.notoriety = f["notoriety"]
        if was_dead is not None and was_dead != s.dead:
            rt._emit("resurrect" if was_dead else "death", body=s.body, x=s.x, y=s.y, z=s.z)
        rt.prune()
        return
    rt.state.upsert_mobile(f["serial"], graphic=f["graphic"], hue=f["hue"],
                           flags=f["flags"], notoriety=f["notoriety"],
                           x=f["x"], y=f["y"], z=f["z"],
                           direction=f["dir"] & 7)


def _h_mobile_move(rt, f):
    """0x77: position/direction update for self or a nearby mobile."""
    s = rt.state.self
    if s.serial == f["serial"]:
        _set_self_position(s, f)
        rt.prune()
    else:
        rt.state.upsert_mobile(f["serial"], x=f["x"], y=f["y"], z=f["z"],
                               direction=f["dir"] & 7)


def _h_mobile_equip(rt, f):
    """0x78: a mobile's equipment list (items parented to the mobile)."""
    if rt.state.self.serial != f["serial"]:
        rt.state.upsert_mobile(f["serial"])
    for e in f["equipment"]:
        rt.state.upsert_item(e["serial"], graphic=e["graphic"],
                             layer=e["layer"], hue=e["hue"], v12=e["v12"],
                             container=f["serial"])


def _h_talk(rt, f):
    """0x1C / 0xAE: speech or system text heard by the client. Type 6 is a
    click label (the server's answer to 0x09, e.g. "Len the banker"); the
    latest one per entity is kept in state.labels."""
    if f["type"] == 6 and f["serial"] not in (0, 0xFFFFFFFF):
        rt.state.labels[f["serial"]] = f["text"]
    rt.state.tracking.on_text(f["serial"], f["text"], rt.state.self.serial)
    rt._emit("speech_heard", serial=f["serial"], name=f["name"],
             type=f["type"], hue=f["hue"], text=f["text"])


def _h_cliloc(rt, f):
    """0xC1 / 0xCC: cliloc message. Consumers match on the number; the text is
    rendered on demand from Cliloc.enu (uo/cliloc.py)."""
    rt._emit("cliloc", serial=f["serial"], name=f["name"], type=f["type"],
             hue=f["hue"], cliloc=f["cliloc"], args=f["args"],
             affix=f["affix"], affix_flags=f["affix_flags"])


def _h_update_name(rt, f):
    rt.state.apply_names([{"serial": f["serial"], "name": f["name"]}])


def _h_character_list(rt, f):
    rt.state.characters = [n for n in f["names"] if n]
    rt._emit("character_list", names=rt.state.characters)


def _h_update_item_sa(rt, f):
    it = rt.state.upsert_item(
        f["serial"], graphic=f["graphic"], amount=f["amount"] or 1,
        x=f["x"], y=f["y"], z=f["z"], dir=f["dir"], hue=f["hue"],
        flags=f["flags"], container=None, data_type=f["data_type"],
        v11=f["v11"])
    rt._emit("item_seen", serial=it.serial, graphic=it.graphic,
             container=None)


def _h_equip_item(rt, f):
    rt.state.update_mobile(f["parent"])
    it = rt.state.upsert_item(f["item"], graphic=f["graphic"],
                              layer=f["layer"], container=f["parent"],
                              hue=f["hue"])
    rt._emit("item_seen", serial=it.serial, graphic=it.graphic,
             container=f["parent"])


def _h_delete(rt, f):
    rt.state.delete(f["serial"])
    rt.state.status_requested.discard(f["serial"])
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
    """0x3C: items of one or more containers. The client clears the first
    container and re-adds the items in packet order (ClassicUO
    UpdateContainedItems / AddItemToContainer), so that order is the
    container's display order, which e.g. a vendor's 0x74 price list refers to.
    The event carries it per container: [[container, [serials...]], ...]."""
    order = {}
    for rec in f["items"]:
        rt.state.upsert_item(
            rec["serial"], graphic=rec["graphic"], amount=rec["amount"],
            x=rec["x"], y=rec["y"], grid=rec["grid"],
            container=rec["container"], hue=rec["hue"], v11=rec["v11"],
            v12=rec["v12"])
        order.setdefault(rec["container"], []).append(rec["serial"])
    rt._emit("container_content", count=len(f["items"]), containers=[[c, s] for c, s in order.items()])


def _h_target_cursor(rt, f):
    """S2C 0x6C. A cursor is up only while cursor_type < 3; type 3 is the
    server cancelling it (ClassicUO TargetManager.SetTargeting:
    IsTargeting = cursorType < TargetType.Cancel)."""
    t = rt.state.target
    t.active, t.target_type = f["cursor_type"] < 3, f["target_type"]
    t.cursor_id, t.cursor_type = f["cursor_id"], f["cursor_type"]
    rt._emit("target", target_type=t.target_type, cursor_id=t.cursor_id,
             cursor_type=t.cursor_type, active=t.active)


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
        return
    _set_self_position(s, f)
    s.stats["graphic"] = f["graphic"]
    rt.prune()


def _h_character_status(rt, f):
    s = rt.state.self
    if s.serial == f["serial"]:
        if f["name"]:
            s.name = f["name"]
        s.hits, s.hits_max = f["hits"], f["hits_max"]
        for k, v in f.items():
            # str/dex/int go to stats (exported); SelfState has no fields for them, so the
            # old setattr kept them out of every snapshot
            if k in ("stam", "stam_max", "mana", "mana_max", "gold", "weight"):
                setattr(s, k, v)
            elif k not in ("serial", "name", "hits", "hits_max", "renamable",
                           "type"):
                s.stats[k] = v
    else:
        rt.state.update_mobile(f["serial"], name=f["name"] or None,
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
    it = rt.state.upsert_item(f["serial"], container=None, data_type=f.get("subtype", 0), **fields)
    rt._emit("item_seen", serial=it.serial, graphic=it.graphic,
             container=None)


def _h_corpse_equipment(rt, f):
    for e in f["equipment"]:
        rt.state.upsert_item(e["serial"], container=f["corpse"],
                             layer=e["layer"])


def _h_healthbar(rt, f):
    m = rt.state.update_mobile(f["serial"])
    if m is None:
        return
    for e in f["entries"]:
        if e["type"] == 1:  # hits-bar poisoned flag (doc §2)
            m.poisoned = bool(e["enabled"])


def _h_display_death(rt, f):
    """0xAF: a mobile died in view (0x1D and 0xFF sub 0xDEAD follow; the
    latter emits `mobile_death`)."""
    rt.remove_dead(f["serial"])


def _h_view_range(rt, f):
    """0xC8: the client's view range; World.ProcessDeletes prunes beyond it."""
    rt.state.view_range = f["range"]
    rt.prune()


def _gump_open(rt, f):
    g = GumpState(serial=f["serial"], gump_id=f["gump_id"], x=f["x"],
                  y=f["y"], layout=f["layout"], lines=f["lines"], open=True,
                  compressed=bool(f.get("compressed")))
    rt.state.gumps[(g.serial, g.gump_id)] = g
    rt._emit("gump_open", serial=g.serial, gump_id=g.gump_id, x=g.x, y=g.y,
             layout=g.layout, lines=g.lines)


def _h_open_menu(rt, f):
    """0x7C item/question menu (Tracking categories on classic servers). The
    menu is reported as an event; answering it (C2S 0x7D) is not built."""
    rt._emit("menu", serial=f["serial"], menu_id=f["menu_id"], title=f["title"],
             gray=f["gray"], entries=f["entries"])


def _h_quest_arrow(rt, f):
    """0xBA quest arrow on/off (display 0 = remove)."""
    rt._emit("quest_arrow", display=bool(f["display"]), x=f["x"], y=f["y"],
             serial=f["serial"])


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
    elif sub == 5:
        rt.prune()   # World.ProcessDeletes (decompiled/process_deletes.c)
    elif sub == 0x15:
        rt.state.apply_names(f["entries"])
        rt._emit("names", count=len(f["entries"]), entries=f["entries"])
    elif sub == 0xDEAD:
        rt.mobile_death(f["serial"], f["corpse"], f["name"])
    elif sub == 0x1A and f.get("mode") == 0:
        rt.state.tracking.on_arrow_set(f)
        rt._emit("quest_arrow_set", **{k: f[k] for k in (
            "arrow_id", "type", "v16", "serial", "x", "y", "z", "text")})
    elif sub == 0x1A and f.get("mode") == 1:
        rt.state.tracking.on_arrow_cancel(f["arrow_id"])
        rt._emit("quest_arrow_cancel", arrow_id=f["arrow_id"])
    elif sub == 0x1A and f.get("mode") == 2:
        rt.state.tracking.on_arrow_cancel()
        rt._emit("quest_arrow_clear")
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
    """C2S 0x02: a walk REQUEST. Self does not move yet: the walk is recorded
    as pending and applied when the server confirms its seq (_h_confirm_walk),
    the same rule as the proxy's MoveAuthority (docs/MOVEMENT.md)."""
    s = rt.state.self
    direction = f["dir"] & 7
    rt.pending_walks.append((f["seq"], direction))
    if len(rt.pending_walks) > PENDING_WALKS_MAX:
        del rt.pending_walks[0]
    s.walk_seq = f["seq"]
    rt._emit("walk", dir=direction, run=bool(f["dir"] & 0x80), seq=f["seq"],
             x=s.x, y=s.y, moved=False)


def _h_dclick(rt, f):
    rt._emit("dclick", serial=f["serial"])


def _query_handler(pid):
    def h(rt, f):
        rt._adopt_self_serial(f["serial"])
        rt.state.census.add(f["serial"], f"0x{pid:02x}")
        if pid == 0x34 and f.get("type") == 4:
            # ClassicUO RequestMobileStatus: HitsRequest Pending -> Received; the client
            # won't send another 0x34 for it until a close-status (bf 000c) resets it
            rt.state.status_requested.add(f["serial"])
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
    """C2S 0xB1: the client (or agent) answered a gump, which closes it (the
    stock client disposes the gump when it sends the response)."""
    g = rt.state.gumps.get((f["serial"], f["gump_id"]))
    if g is not None:
        g.responses += 1
        g.open = False
    rt._emit("gump_response", serial=f["serial"], gump_id=f["gump_id"],
             button_id=f["button_id"], switches=f["switches"],
             texts=f["texts"])


def _h_target_response(rt, f):
    rt.state.target.active = False
    rt._emit("target_response", serial=f["serial"], cursor_id=f["cursor_id"],
             target_type=f["target_type"], x=f["x"], y=f["y"], z=f["z"],
             graphic=f["graphic"])


def _h_lift(rt, f):
    rt._emit("lift", serial=f["serial"], amount=f["amount"])


def _h_drop(rt, f):
    rt._emit("drop", serial=f["serial"], x=f["x"], y=f["y"], z=f["z"],
             grid=f["grid"], container=f["container"])


def _h_equip_request(rt, f):
    rt._emit("equip_request", serial=f["serial"], layer=f["layer"],
             container=f["container"])


def _h_buy_list(rt, f):
    rt._emit("buy_list", container=f["container"], items=f["items"])


def _h_buy_request(rt, f):
    rt._emit("buy", vendor=f["vendor"], items=f["items"])


def _h_text_command(rt, f):
    rt._emit("command", type=f["type"], text=f["text"])


def _extended_handler(direction):
    """0xBF: context menu subs become events; other subs are counted as
    unhandled 0xBF (as before this parser existed)."""
    def h(rt, f):
        sub = f["sub"]
        if sub == 0x14 and direction == S2C:
            rt._emit("popup", serial=f["serial"], entries=f["entries"])
        elif sub == 0x08 and direction == S2C and "map" in f:
            # ClassicUO World.MapIndex: a different facet clears every mobile but
            # self and every item self doesn't carry (EnterWorld sets 0 first)
            prev = rt.state.self.map if rt.state.self.map is not None else 0
            rt.state.self.map = f["map"]
            if f["map"] != prev:
                for serial in rt.state.clear_facet(prev):
                    rt._emit("prune", serial=serial, why="facet")
            rt._emit("map_change", map=f["map"])
        elif sub == 0x04 and direction == S2C and "gump_id" in f:
            closed = 0
            for g in rt.state.gumps.values():
                if g.gump_id == f["gump_id"] and g.open:
                    g.open = False
                    closed += 1
            rt._emit("gump_close", gump_id=f["gump_id"], button=f["button"], closed=closed, by="server")
        elif sub == 0x13 and direction == C2S:
            rt._emit("popup_request", serial=f["serial"])
        elif sub == 0x15 and direction == C2S:
            rt._emit("popup_select", serial=f["serial"], index=f["index"])
        elif sub == 0x0C and direction == C2S and "serial" in f:
            rt.state.status_requested.discard(f["serial"])   # SendCloseStatus: HitsRequest -> None
        else:
            rt.unhandled[(direction, 0xBF)] += 1
    return h


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
    0x77: _h_mobile_move,
    0x78: _h_mobile_equip,
    0x1C: _h_talk,
    0xAE: _h_talk,
    0x98: _h_update_name,
    0xA9: _h_character_list,
    0xC1: _h_cliloc,
    0xCC: _h_cliloc,
    0x74: _h_buy_list,
    0xBF: _extended_handler(S2C),
    0x7C: _h_open_menu,
    0xBA: _h_quest_arrow,
    0xAF: _h_display_death,
    0xC8: _h_view_range,
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
    0x07: _h_lift,
    0x08: _h_drop,
    0x13: _h_equip_request,
    0x3B: _h_buy_request,
    0x12: _h_text_command,
    0xBF: _extended_handler(C2S),
    0xFF: _h_dialect_c2s,
}

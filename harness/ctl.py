"""Overseer control CLI (docs/OVERSEER.md): the AI overseer's hands and ears.

Tasks stay programmatic (loop_lumber.py, errand_bank.py); an AI overseer
supervises them through this CLI. There is no daemon: the SQLite store
(harness/memory.py) is the bus. Runners and task_wrap.py post junctures, the
user posts chat, and `ctl wait` blocks until one of them needs the overseer.

Usage: python harness/ctl.py [--db P] [--state-port N] [--control-port N] <cmd> ...
  status                      proxy snapshot + running tasks + open junctures
  run <task> [args...]        start a whitelisted task (lumber, bank) detached
  stop [task_id]              stop the running task (-> task_failed juncture)
  wait [--timeout S] [--include-info]
                              block until a juncture (>= attention) or user chat
  ack <juncture_id>           close a juncture
  junctures [--open] [--after N] [--limit N]
  chat [--after N] [--limit N] [--role R]
  say <text> | think <text> | note-action <text>
                              chat rows (role overseer; message/thought/action)
  act <name> [args]           one stock action through the proxy control port:
                              walk <dir 0-7> [n] [--run], say <allowlisted>,
                              dclick <serial>, single_click <serial>, open_door,
                              target_cancel, goto <x> <y> | goto <mobile serial>
                              [--range R], menu <serial>, menu_pick <serial> <index>,
                              gump <serial> <button>
  journal [--n N]             recent server messages, gumps, menus, vendor lists
Every call prints exactly one JSON object on stdout; exit 0 iff "ok" is true.
Global options go before the command.

Safety: `act gump` refuses the captcha (it is always a human's, ANTICHEAT.md
§8.8), refuses gumps without reply buttons (§8.13 decoys flag any bot reply),
refuses buttons the layout doesn't offer, and on a gump that mentions
renouncing Young status allows only closing it (button 0). No raw packets;
speech is allowlisted; nothing is sent while a task runs (one character, no
interleaving). Opening a vendor's Buy list sends nothing further: the stock
client sends no packet when a shop window is closed without buying
(ClassicUO ShopGump.cs:590-610, Send_BuyRequest only on Accept).
"""
import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import actions  # noqa: E402
import nav  # noqa: E402
import task_wrap as tw  # noqa: E402
from humanize import PROFILES, Human  # noqa: E402
from memory import DEFAULT_DB, Memory  # noqa: E402
from uo import cliloc as cliloc_mod  # noqa: E402
from uo.gumps import controls as gump_controls, parse_layout, reply_fields as gump_reply_fields  # noqa: E402

HOST = "127.0.0.1"
TASKS = {"lumber": os.path.join(HERE, "loop_lumber.py"),
         "bank": os.path.join(HERE, "errand_bank.py")}
# Tests only: JSON {name: script path} replacing TASKS. Production never sets it.
TEST_TASKS_ENV = "UO_CTL_TEST_TASKS"
LOG_DIR = os.path.join(ROOT, "logs", "tasks")
# In-game speech allowlist (docs/PLAN.md "In-game speech is allowlisted keywords/
# commands only"; LUMBER_LOOP.md adds `room`). No code allowlist existed before
# this; these are the only phrases `act say` sends.
SPEECH_ALLOWLIST = ("bank", "room", "hello")

HEARTBEAT_KEY = "overseer_heartbeat"
JUNCTURE_CURSOR_KEY = "overseer_juncture_cursor"
CHAT_CURSOR_KEY = "overseer_chat_cursor"
SEVERITY_RANK = {"info": 0, "attention": 1, "urgent": 2}
WAIT_MAX_EVENTS = 20
NEARBY_RANGE = 18
NEARBY_MAX = 30
WALK_MAX_STEPS = 20
LAYER_BACKPACK = 0x15
DROP_AUTO = 0x7FFFFFFF               # drop-into-container auto position (demo capture 204225, loop_lumber)
DIR_NAMES = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
# ClassicUO Game/Data/Layers.cs (0x1A-0x1C are vendor containers; 0x1D is the bank box)
LAYER_NAMES = {1: "one_handed", 2: "two_handed", 3: "shoes", 4: "pants", 5: "shirt", 6: "helmet",
               7: "gloves", 8: "ring", 9: "talisman", 0x0A: "necklace", 0x0B: "hair", 0x0C: "waist",
               0x0D: "torso", 0x0E: "bracelet", 0x0F: "face", 0x10: "beard", 0x11: "tunic",
               0x12: "earrings", 0x13: "arms", 0x14: "cloak", 0x15: "backpack", 0x16: "robe",
               0x17: "skirt", 0x18: "legs", 0x19: "mount", 0x1D: "bank"}
# RunUO Notoriety constants (Innocent 1 .. Invulnerable 7) [INFERENCE: not in the
# local ClassicUO tree; the client only switches on the named enum].
NOTORIETY = {1: "innocent", 2: "ally", 3: "attackable", 4: "criminal", 5: "enemy",
             6: "murderer", 7: "invulnerable"}
ACTS = ("walk", "say", "dclick", "single_click", "open_door", "target_cancel",
        "goto", "menu", "menu_pick", "gump", "unequip", "equip", "warmode", "attack", "loot",
        "target", "cast", "buy", "use", "drop")
PACK_ITEMS_MAX = 60                  # status.backpack.items
CONTAINER_ITEMS_MAX = 60             # status.containers[].items
# Combat (user decision 2026-09-30): hostile monsters may be fought and looted; players never
# (Test Shard CoC, ANTICHEAT.md §8.17). Notoriety 3-6 is attackable without a criminal flag;
# 1 (innocent: players' pets) and 2 (ally) are criminal to attack, 7 is invulnerable.
ATTACKABLE_NOTORIETY = frozenset([3, 4, 5, 6])
ATTACK_MIN_HP = 0.3                  # refuse to start a fight below this share of max hits
DENY_TELEPORT_GRACE_S = 0.4           # after a walk deny, a teleporter may still move us (agent_link)
CORPSE_GRAPHIC = 0x2006
LOOT_RANGE = 2                       # tiles; the server's own limit is similar [INFERENCE]
LOOT_MAX_ITEMS = 25
GOLD_GRAPHIC = 0x0EED
LAYER_BANK = 0x1D
# ClassicUO Game/Data/SpellsMagery.cs (ids 1-64); the Outlands client casts with 0xFF sub 4
# (observed live, session 20260928_164548: ids 5 and 15)
MAGERY_SPELLS = (
    "Clumsy", "Create Food", "Feeblemind", "Heal", "Magic Arrow", "Night Sight", "Reactive Armor", "Weaken",
    "Agility", "Cunning", "Cure", "Harm", "Magic Trap", "Magic Untrap", "Protection", "Strength",
    "Bless", "Fireball", "Magic Lock", "Poison", "Telekinesis", "Teleport", "Unlock", "Wall of Stone",
    "Arch Cure", "Arch Protection", "Curse", "Fire Field", "Greater Heal", "Lightning", "Mana Drain", "Recall",
    "Blade Spirits", "Dispel Field", "Incognito", "Magic Reflection", "Mind Blast", "Paralyze", "Poison Field",
    "Summon Creature", "Dispel", "Energy Bolt", "Explosion", "Invisibility", "Mark", "Mass Curse",
    "Paralyze Field", "Reveal", "Chain Lightning", "Energy Field", "Flamestrike", "Gate Travel", "Mana Vampire",
    "Mass Dispel", "Meteor Swarm", "Polymorph", "Earthquake", "Energy Vortex", "Resurrection", "Air Elemental",
    "Summon Daemon", "Earth Elemental", "Fire Elemental", "Water Elemental")
CAST_CURSOR_WAIT_S = 4.0             # a spell's target cursor comes after its cast delay
BUY_CLILOC = 3006103                 # context menu "Buy"
VENDOR_RANGE = 12                    # 13 tiles got "too far away" live (docs/LUMBER_LOOP.md §13)
SORTED_BUY_CONTAINER = 0x2AF8        # ClassicUO BuyList: this container sorts by x; others map reversed
POLICY_PATH = os.path.join(HERE, "data", "policy.json")
CAPTCHA_GUMP_ID = 0x00000001          # lumber.json captcha.gump_id; human-only
GUMP_TEXT_MAX = 239                  # chars per gump text entry (the client's text box limit)
RENOUNCE_WORDS = ("renounce",)        # Young renounce prompt (clilocs 502085/3006307): close only
GOTO_MAX_MOVES = 400
GOTO_Z_TOL = 10                      # goto --z / ground item: stand within this of the target z
GROUND_RANGE = 12                    # status: ground items within this many tiles
GROUND_MAX = 20
EVENT_WAIT_S = 3.0
# What `journal` shows: what a player reads on screen (messages, gumps, menus).
JOURNAL_EVS = ("speech_heard", "cliloc", "gump_open", "gump_response", "popup", "buy_list",
               "menu", "quest_arrow", "quest_arrow_set", "target", "map_change")


class CtlError(Exception):
    pass


# ------------------------------------------------------------------- store
def heartbeat(mem: Memory):
    tw.meta_set(mem, HEARTBEAT_KEY, f"{time.time():.2f}")


def running_tasks(mem: Memory) -> list:
    """Live task entries. An entry whose wrapper is gone without reporting
    (crash, reboot) is removed and reported as task_failed once."""
    alive = []
    for e in tw.task_entries(mem):
        if tw.entry_alive(e):
            alive.append(e)
        elif tw.remove_entry(mem, e["task_id"]):
            mem.juncture("ctl", "task_failed",
                         f"{e['task']} ended without a report (its wrapper is gone)"[:300],
                         severity="attention",
                         data={"task_id": e["task_id"], "task": e["task"], "args": e.get("args", []),
                               "exit_code": None, "log": e.get("log"), "tail": tw.read_tail(e.get("log", ""))})
    return alive


def task_whitelist() -> dict:
    raw = os.environ.get(TEST_TASKS_ENV)
    return json.loads(raw) if raw else dict(TASKS)


def open_juncture_count(mem: Memory) -> int:
    return mem.con.execute("SELECT COUNT(*) FROM junctures WHERE acked_t IS NULL").fetchone()[0]


# ------------------------------------------------------------------- proxy
def state_query(port: int, timeout: float = 5.0) -> dict:
    """One state-port snapshot without the event backlog."""
    with socket.create_connection((HOST, port), timeout=timeout) as s:
        s.sendall((json.dumps({"op": "state", "since": 1 << 62}) + "\n").encode())
        resp = json.loads(s.makefile("rb").readline() or b"{}")
    return resp


class Control:
    """Proxy control port, framed like agent_link.Link.send (u16be length + packet;
    reply u16be length + text). Gate refusals are returned, never waited out."""

    def __init__(self, port: int):
        self.sock = socket.create_connection((HOST, port), timeout=10)

    def send(self, pkt: bytes) -> str:
        self.sock.sendall(len(pkt).to_bytes(2, "big") + pkt)
        n = int.from_bytes(self._recv(2), "big")
        return self._recv(n).decode()

    def _recv(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise CtlError("proxy closed the control connection")
            buf += chunk
        return buf

    def close(self):
        self.sock.close()


class StateConn:
    """Persistent state-port connection for step outcomes."""

    def __init__(self, port: int):
        self.port = port
        self.sock = socket.create_connection((HOST, port), timeout=10)
        self.f = self.sock.makefile("rb")

    def state(self) -> dict:
        self.sock.sendall((json.dumps({"op": "state", "since": 1 << 62}) + "\n").encode())
        resp = json.loads(self.f.readline() or b"{}")
        if not resp.get("ok"):
            raise CtlError(f"state port: {resp.get('error')}")
        return resp

    def events(self, since: int) -> tuple[list, int]:
        """(event envelopes with seq >= since, next cursor), no world snapshot."""
        self.sock.sendall((json.dumps({"op": "state", "since": since, "snapshot": False}) + "\n").encode())
        resp = json.loads(self.f.readline() or b"{}")
        if not resp.get("ok"):
            raise CtlError(f"state port: {resp.get('error')}")
        return resp.get("events") or [], resp.get("next", since)

    def mark(self) -> int:
        return self.events(1 << 62)[1]

    def intent(self, text: str | None, kind: str | None = None, target=None, target_serial=None) -> bool:
        """Tell the viz what the overseer is doing (proxy-side only; SessionTap.set_intent).
        target: (x, y) tile; target_serial: an entity the marker follows. Never fails the act:
        if the proxy drops the connection (a proxy that can't take the intent), this reconnects
        and retries without target_serial, so the act can go on using this connection."""
        body = None
        if text is not None:
            body = {"text": text[:200], "loop": "overseer"}
            if kind:
                body["kind"] = kind
            if target is not None and None not in tuple(target)[:2]:
                body["target"] = [int(target[0]), int(target[1])]
            if target_serial is not None:
                body["target_serial"] = f"0x{_serial(target_serial):08X}"
        tries = [body] + ([{k: v for k, v in body.items() if k != "target_serial"}]
                          if body and "target_serial" in body else [])
        for b in tries:
            try:
                self.sock.sendall((json.dumps({"op": "intent", "intent": b}) + "\n").encode())
                line = self.f.readline()
                if line:
                    return bool(json.loads(line).get("ok"))
            except (OSError, ValueError):
                pass
            self._reconnect()
        return False

    def _reconnect(self):
        try:
            self.sock.close()
        except OSError:
            pass
        self.sock = socket.create_connection((HOST, self.port), timeout=10)
        self.f = self.sock.makefile("rb")

    def wait_events(self, since: int, pred, timeout: float = EVENT_WAIT_S) -> list:
        """World events after `since` until pred(events) holds or timeout."""
        end, got = time.monotonic() + timeout, []
        while True:
            evs, since = self.events(since)
            got += [e["data"] for e in evs if e.get("origin") == "world"]
            if pred(got) or time.monotonic() > end:
                return got
            time.sleep(0.1)

    def close(self):
        self.sock.close()


_CLILOC = None


def cliloc_text(number, args="") -> str:
    """Render a cliloc like the client (install-dir Cliloc.enu, read-only)."""
    global _CLILOC
    if _CLILOC is None:
        try:
            _CLILOC = cliloc_mod.load()
        except OSError:
            _CLILOC = {}
    return cliloc_mod.translate(_CLILOC, int(number), args or "")


def gump_view(g: dict) -> dict:
    """What the overseer needs to reason about a gump: ids, text, clilocs
    rendered, reply buttons, whether it can be closed, and `controls`: where
    each button and text entry is with the texts on its row (uo.gumps.controls;
    `near[].dx` > 0 means the text is right of the control)."""
    layout = g.get("layout") or ""
    lay = parse_layout(layout)
    texts = [t for t in (g.get("lines") or []) if t] + [cliloc_text(c) for c in lay["clilocs"]]
    serial, gid = g.get("serial"), g.get("gump_id")
    return {"serial": f"0x{_serial(serial):08X}" if serial is not None else None,
            "gump_id": f"0x{_serial(gid):08X}" if gid is not None else None,
            "texts": texts, "buttons": lay["buttons"], "entries": lay["entries"],
            "controls": gump_controls(layout, g.get("lines") or [], cliloc_text),
            "closable": "noclose" not in layout.lower()}


def journal_view(d: dict) -> dict:
    """One world event as the player would read it."""
    ev = d.get("ev")
    if ev == "speech_heard":
        return {"ev": ev, "from": d.get("name"), "text": d.get("text")}
    if ev == "cliloc":
        return {"ev": ev, "from": d.get("name"), "cliloc": d.get("cliloc"),
                "text": cliloc_text(d.get("cliloc"), d.get("args"))}
    if ev == "gump_open":
        return {"ev": ev, **gump_view(d)}
    if ev == "popup":
        return {"ev": ev, "serial": f"0x{_serial(d['serial']):08X}",
                "entries": [{"index": e.get("index"), "text": cliloc_text(e.get("cliloc")),
                             "disabled": bool((e.get("flags") or 0) & 0x01)} for e in d.get("entries") or []]}
    if ev == "buy_list":
        return {"ev": ev, "container": d.get("container"),
                "items": [{"name": item_name(i.get("name")), "price": i.get("price")} for i in d.get("items") or []]}
    return {k: v for k, v in d.items() if k not in ("layout",)}


def item_name(name: str) -> str:
    """Vendor list names are often cliloc numbers sent as text."""
    s = (name or "").strip("\x00").strip()
    return cliloc_text(int(s)) if s.isdigit() else s


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def summarize(resp: dict) -> dict:
    mv = resp.get("movement") or {}
    world = resp.get("world") or {}
    me = world.get("self") or {}
    pos = mv.get("pos")
    self_serial = mv.get("self_serial")
    labels = world.get("labels") or {}
    mobiles = []
    for key, m in (world.get("mobiles") or {}).items():
        s = _serial(key)
        if s == self_serial or m.get("x") is None:
            continue
        dist = nav.chebyshev((m["x"], m["y"]), (pos[0], pos[1])) if pos else None
        if dist is not None and dist > NEARBY_RANGE:
            continue
        mobiles.append({"serial": f"0x{s:08X}", "name": m.get("name"), "label": labels.get(key),
                        "graphic": m.get("graphic"),
                        "notoriety": m.get("notoriety"), "notoriety_name": NOTORIETY.get(m.get("notoriety")),
                        "hits": m.get("hits"), "hits_max": m.get("hits_max"),
                        "x": m["x"], "y": m["y"], "z": m.get("z"), "dist": dist})
    mobiles.sort(key=lambda m: (m["dist"] is None, m["dist"] or 0))
    items = world.get("items") or {}
    pack = next((_serial(k) for k, it in items.items()
                 if it.get("layer") == LAYER_BACKPACK and it.get("container") is not None
                 and _serial(it["container"]) == self_serial), None)
    counts, pack_items = {}, []
    if pack is not None:
        for k, it in _pack_items(items, pack):
            g = counts.setdefault(f"0x{it['graphic']:04X}", {"stacks": 0, "amount": 0})
            g["stacks"] += 1
            g["amount"] += it.get("amount") or 1
            pack_items.append({"serial": k, "graphic": f"0x{it['graphic']:04X}",
                               "name": item_label(it), "amount": it.get("amount") or 1,
                               "in": None if _serial(it["container"]) == pack else it["container"]})
    gumps = [gump_view(g) for g in world.get("gumps") or [] if g.get("open")]
    ground = []
    if pos:
        for key, it in items.items():
            x, y = it.get("x"), it.get("y")
            if it.get("container") is not None or x is None:
                continue
            dist = nav.chebyshev((x, y), (pos[0], pos[1]))
            if dist <= GROUND_RANGE:
                ground.append({"serial": key, "name": it.get("name") or _tile_name(it.get("graphic")),
                               "graphic": None if it.get("graphic") is None else f"0x{it['graphic']:04X}",
                               "x": x, "y": y, "z": it.get("z"), "amount": it.get("amount"), "dist": dist})
        ground.sort(key=lambda g: g["dist"])
    hp = lambda a, b: None if me.get(a) is None else [me.get(a), me.get(b)]  # noqa: E731
    equipment = {}
    for key, it in items.items():
        if it.get("container") is not None and _serial(it["container"]) == self_serial and it.get("layer"):
            equipment[LAYER_NAMES.get(it["layer"], f"layer_0x{it['layer']:02X}")] = {
                "serial": key, "graphic": None if it.get("graphic") is None else f"0x{it['graphic']:04X}",
                "name": it.get("name") or _tile_name(it.get("graphic"))}
    return {
        "name": me.get("name"), "serial": me.get("serial"),
        "pos": pos, "facet": me.get("map"),
        "dead": me.get("dead"), "body": None if me.get("body") is None else f"0x{me['body']:04X}",
        "hits": hp("hits", "hits_max"), "stam": hp("stam", "stam_max"), "mana": hp("mana", "mana_max"),
        "weight": me.get("weight"), "gold": me.get("gold"), "warmode": me.get("warmode"),
        "equipment": dict(sorted(equipment.items())),
        "skills": _skills(me),
        "stats": _stats(me),
        "buffs": _buffs(world, me),
        "movement": {k: mv.get(k) for k in ("inflight", "stalled", "resync_pending", "client_stale")},
        "gate": resp.get("gate"),
        "intent": resp.get("intent"), "intents": (resp.get("intents") or [])[-5:],
        "mobiles": mobiles[:NEARBY_MAX],
        "backpack": {"serial": None if pack is None else f"0x{pack:08X}", "counts": counts,
                     "items": pack_items[:PACK_ITEMS_MAX]},
        "containers": _containers(world, items, self_serial, pack),
        "target": world.get("target"),
        "gumps_open": gumps,
        "ground_items": ground[:GROUND_MAX],
    }


def _containers(world: dict, items: dict, me, pack) -> list:
    """Contents of containers other than the backpack that the server has shown
    us: your bank box (once opened), and every container opened this session
    (0x24: chests, corpses; vendor stock excluded). Items at any bag depth:
    serial, graphic, name, amount, and `in` when inside a sub-bag. Contents are
    as last shown, so a container you walked away from may be stale."""
    roots = []
    for key, it in items.items():
        if it.get("layer") == LAYER_BANK and it.get("container") is not None and _serial(it["container"]) == me:
            roots.append((_serial(key), "bank"))
    for s in world.get("containers") or []:
        s = _serial(s)
        it = items.get(f"0x{s:08X}")
        if s == pack or it is None or s in (r[0] for r in roots) or 0x1A <= (it.get("layer") or 0) <= 0x1C:
            continue                          # the backpack is listed already; 0x1A-0x1C are vendor stock
        roots.append((s, "corpse" if it.get("graphic") == CORPSE_GRAPHIC else
                      ("ground" if it.get("x") is not None and it.get("container") is None else "container")))
    out = []
    for root, kind in roots:
        box = items.get(f"0x{root:08X}") or {}
        rows = [{"serial": k, "graphic": f"0x{it['graphic']:04X}", "name": item_label(it),
                 "amount": it.get("amount") or 1,
                 "in": None if _serial(it["container"]) == root else it["container"]}
                for k, it in _pack_items(items, root)]
        out.append({"serial": f"0x{root:08X}", "kind": kind,
                    "name": item_label(box) if box.get("graphic") is not None else None,
                    "count": len(rows), "items": rows[:CONTAINER_ITEMS_MAX]})
    return out


def _stats(me: dict) -> dict:
    """Base stats and the rest of the 0x11 status: Str/Dex/Int, stat cap, luck,
    resists, damage, followers, max weight (absent until the server sends them)."""
    s = me.get("stats") or {}
    out = {k: s[k] for k in ("str", "dex", "int", "stats_cap", "luck", "weight_max", "tithing") if k in s}
    res = {k.removesuffix("_resist"): s[k] for k in ("physical_resist", "fire_resist", "cold_resist",
                                                      "poison_resist", "energy_resist") if k in s}
    if res:
        out["resists"] = res
    if "damage_min" in s:
        out["damage"] = [s["damage_min"], s.get("damage_max")]
    if "followers" in s:
        out["followers"] = [s["followers"], s.get("followers_max")]
    return out


def _buffs(world: dict, me: dict) -> list:
    """Your active buffs/debuffs (Outlands 0xFF sub 8, e.g. "Stationary Penalty").
    `description` is the server's text with its {value} placeholder; `raw` keeps
    the numeric fields whose meaning isn't decoded yet (f2 looks like a count)."""
    out = []
    for icon, b in ((world.get("buffs") or {}).get(me.get("serial") or "", {}) or {}).items():
        title = b.get("title") or (cliloc_text(b["cliloc"]) if b.get("cliloc") else "")
        out.append({"icon": int(icon), "title": title, "description": b.get("description") or None,
                    "timers_s": [t.get("seconds") for t in b.get("timers") or []],
                    "raw": {k: b.get(k) for k in ("f1", "f2", "f3", "f4", "category", "mode", "scalar")}})
    return sorted(out, key=lambda b: b["icon"])


def _skills(me: dict) -> dict:
    """{skill name: value} for skills above 0 (the server sends tenths). Names
    from the server's own list if this session got one, else from the
    client's skills.mul (uomap.skill_names; install dir, read-only)."""
    names = me.get("skill_names") or []
    if not names:
        try:
            import uomap
            names = uomap.skill_names()
        except OSError:
            names = []
    out = {}
    for sid, sk in (me.get("skills") or {}).items():
        if sk.get("value"):
            i = int(sid)
            out[names[i] if i < len(names) else f"skill #{i}"] = sk["value"] / 10
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _pack_items(items: dict, pack: int):
    """(serial key, item) for everything in the backpack, any bag depth."""
    parent = {_serial(k): (_serial(it["container"]) if it.get("container") else None) for k, it in items.items()}
    for k, it in items.items():
        c, depth = parent[_serial(k)], 0
        while c is not None and c != pack and depth < 8:
            c, depth = parent.get(c), depth + 1
        if c == pack and it.get("graphic") is not None:
            yield k, it


def item_label(it: dict) -> str | None:
    """What an item is called: its clicked name, else the tiledata name."""
    name = it.get("name") or _tile_name(it.get("graphic")) or ""
    return item_name(name) or None


def _tile_name(graphic):
    """Tiledata name of an item graphic (install dir, read-only), or None."""
    if graphic is None:
        return None
    try:
        import uomap
        it = uomap.tiledata().item(graphic)
    except (OSError, ValueError):
        return None
    return it.name if it else None


# ------------------------------------------------------------------ commands
def cmd_status(a, mem):
    hb = tw.meta_get(mem, HEARTBEAT_KEY)
    out = {"tasks": running_tasks(mem), "open_junctures": open_juncture_count(mem),
           "overseer_heartbeat": None if hb is None else float(hb)}
    try:
        resp = state_query(a.state_port)
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"proxy state port {a.state_port} unreachable: {e}", **out}
    if not resp.get("ok"):
        return {"ok": False, "error": f"state port: {resp.get('error')}", "gate": resp.get("gate"), **out}
    return {"ok": True, **summarize(resp), **out}


def cmd_run(a, mem):
    heartbeat(mem)
    tasks = task_whitelist()
    if a.task not in tasks:
        raise CtlError(f"unknown task {a.task!r}; allowed: {sorted(tasks)}")
    alive = running_tasks(mem)
    if alive:
        raise CtlError(f"task {alive[0]['task_id']} is running; one task at a time (stop it first)")
    task_id = f"{a.task}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    log = os.path.join(a.log_dir, f"{task_id}.log")
    user_args = list(a.args)
    argv = list(user_args)
    for flag, val in (("--control-port", a.control_port), ("--state-port", a.state_port), ("--memory", a.db)):
        if flag not in argv:
            argv += [flag, str(val)]
    entry = {"task_id": task_id, "task": a.task, "args": user_args, "pid": None,
             "started": time.time(), "log": log}

    def add(cur):
        live = [t for t in cur if tw.entry_alive(t)]
        if live:
            return cur, live[0]["task_id"]
        return cur + [entry], None
    busy = tw.meta_update_json(mem, tw.TASKS_KEY, add, [])
    if busy:
        raise CtlError(f"task {busy} is running; one task at a time (stop it first)")
    os.makedirs(a.log_dir, exist_ok=True)
    spec = {"task_id": task_id, "task": a.task, "script": os.path.abspath(tasks[a.task]),
            "args": user_args, "argv": argv, "log": log, "db": os.path.abspath(a.db)}
    kw = {"creationflags": tw.DETACHED_PROCESS | tw.CREATE_NEW_PROCESS_GROUP} if tw.WINDOWS \
        else {"start_new_session": True}
    try:
        p = subprocess.Popen([sys.executable, os.path.join(HERE, "task_wrap.py"), "--spec", json.dumps(spec)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             cwd=ROOT, close_fds=True, **kw)
    except OSError as e:
        tw.remove_entry(mem, task_id)
        raise CtlError(f"could not start the task wrapper: {e}")
    tw.patch_entry(mem, task_id, pid=p.pid, pid_created=tw.proc_created(p.pid))
    mem.chat_post("overseer", f"run {a.task} {' '.join(user_args)}".strip() + f" ({task_id})", "action",
                  data={"cmd": "run", "task_id": task_id, "task": a.task, "args": user_args})
    return {"ok": True, "task_id": task_id, "task": a.task, "args": user_args, "pid": p.pid, "log": log}


def _task_juncture(mem, task_id):
    rows = mem.con.execute("SELECT id FROM junctures WHERE kind IN ('task_done','task_failed') "
                           "ORDER BY id DESC LIMIT 50").fetchall()
    for (jid,) in rows:
        j = mem.junctures(after_id=jid - 1, limit=1)[0]
        if j["data"].get("task_id") == task_id:
            return j
    return None


def cmd_stop(a, mem):
    heartbeat(mem)
    alive = running_tasks(mem)
    if a.task_id:
        alive = [t for t in alive if t["task_id"] == a.task_id]
    if not alive:
        raise CtlError("no running task" + (f" {a.task_id}" if a.task_id else ""))
    e = alive[0]
    tid = e["task_id"]
    tw.meta_set(mem, tw.STOP_KEY, json.dumps({"task_id": tid, "t": time.time()}))
    mem.chat_post("overseer", f"stop {tid}", "action", data={"cmd": "stop", "task_id": tid})
    end = time.monotonic() + a.grace
    while time.monotonic() < end:
        if not any(t["task_id"] == tid for t in tw.task_entries(mem)):
            return {"ok": True, "task_id": tid, "forced": False, "juncture": _task_juncture(mem, tid)}
        time.sleep(0.2)
    # the wrapper didn't finish in time: end both processes ourselves
    cur = next((t for t in tw.task_entries(mem) if t["task_id"] == tid), e)
    tw.terminate(cur.get("child_pid"), cur.get("child_created"))
    tw.terminate(cur.get("pid"), cur.get("pid_created"))
    if tw.remove_entry(mem, tid):
        tw.meta_set(mem, tw.STOP_KEY, None)
        tw.post_end(mem, cur, -1, True, source="ctl", note=" (forced)")
    return {"ok": True, "task_id": tid, "forced": True, "juncture": _task_juncture(mem, tid)}


# A task's end always wakes `wait`, whatever its severity: it answers the
# overseer's own `run` (task_done is `info`; live 2026-09-30 the overseer sat in
# `wait` after its trip had finished).
ALWAYS_WAKE = ("task_done", "task_failed")


def _open_junctures(mem, after_id, min_rank, limit):
    sev = [s for s, r in SEVERITY_RANK.items() if r >= min_rank]
    q = ("SELECT id FROM junctures WHERE id > ? AND acked_t IS NULL AND (severity IN (%s) OR kind IN (%s)) "
         "ORDER BY id LIMIT ?" % (",".join("?" * len(sev)), ",".join("?" * len(ALWAYS_WAKE))))
    ids = [r[0] for r in mem.con.execute(q, (after_id, *sev, *ALWAYS_WAKE, limit))]
    return [mem.junctures(after_id=i - 1, limit=1)[0] for i in ids]


def cmd_wait(a, mem):
    jcur = int(tw.meta_get(mem, JUNCTURE_CURSOR_KEY, "0"))
    ccur = int(tw.meta_get(mem, CHAT_CURSOR_KEY, "0"))
    min_rank = 0 if a.include_info else SEVERITY_RANK["attention"]
    end = None if a.timeout <= 0 else time.monotonic() + a.timeout
    while True:
        heartbeat(mem)
        js = _open_junctures(mem, jcur, min_rank, WAIT_MAX_EVENTS)
        cs = mem.chat(after_id=ccur, limit=WAIT_MAX_EVENTS, role="user")
        if js or cs:
            if js:
                jcur = js[-1]["id"]
                tw.meta_set(mem, JUNCTURE_CURSOR_KEY, str(jcur))
            if cs:
                ccur = cs[-1]["id"]
                tw.meta_set(mem, CHAT_CURSOR_KEY, str(ccur))
            events = sorted([{"type": "juncture", **j} for j in js] + [{"type": "chat", **c} for c in cs],
                            key=lambda ev: ev["t"])
            return {"ok": True, "event": events[0], "events": events,
                    "cursors": {"juncture": jcur, "chat": ccur}}
        if end is not None and time.monotonic() >= end:
            return {"ok": True, "event": None, "cursors": {"juncture": jcur, "chat": ccur}}
        time.sleep(a.poll)


def cmd_ack(a, mem):
    heartbeat(mem)
    ok = mem.juncture_ack(a.juncture_id)
    return {"ok": ok, "id": a.juncture_id, **({} if ok else {"error": "no such open juncture"})}


def cmd_junctures(a, mem):
    return {"ok": True, "junctures": mem.junctures(after_id=a.after, open_only=a.open, limit=a.limit)}


def cmd_chat(a, mem):
    return {"ok": True, "chat": mem.chat(after_id=a.after, limit=a.limit, role=a.role)}


def _post(kind):
    def cmd(a, mem):
        heartbeat(mem)
        text = " ".join(a.text).strip()
        if not text:
            raise CtlError("empty text")
        return {"ok": True, "id": mem.chat_post("overseer", text, kind), "kind": kind}
    return cmd


# ---------------------------------------------------------------------- act
def _parse_serial(s: str) -> int:
    try:
        v = int(s, 0)
    except ValueError:
        raise CtlError(f"bad serial {s!r} (hex 0x... or decimal)")
    if not 0 < v <= 0xFFFFFFFF:
        raise CtlError(f"serial {s!r} out of range")
    return v


def _act_walk(a, ctl: Control, stc: StateConn) -> dict:
    if not 1 <= len(a.args) <= 2:
        raise CtlError("walk <dir 0-7> [n]")
    try:
        d = int(a.args[0])
        n = int(a.args[1]) if len(a.args) > 1 else 1
    except ValueError:
        raise CtlError("walk <dir 0-7> [n]: integers")
    if not 0 <= d <= 7:
        raise CtlError(f"direction {d} out of range 0-7")
    if not 1 <= n <= WALK_MAX_STEPS:
        raise CtlError(f"steps {n} out of range 1-{WALK_MAX_STEPS}")
    human = Human(a.human, seed=a.seed)
    pos = stc.state()["movement"].get("pos")
    if pos is None:
        raise CtlError("player position unknown")
    start, moved, outcomes, stop = list(pos), 0, [], None
    pkt = actions.walk(d, run=a.run)
    for _ in range(n + 2):          # a turn costs one extra send
        before = pos
        for _ in range(40):          # proxy pacing / resync gates, like Mover.step
            resp = ctl.send(pkt)
            if not resp.startswith(("ERR walk gated: pacing", "ERR walk gated: awaiting")):
                break
            time.sleep(0.1)
        if resp != "OK":
            stop = resp
            break
        end = time.monotonic() + 3.0
        while True:
            st = stc.state()
            if st["movement"].get("inflight", 0) == 0 or time.monotonic() > end:
                break
            time.sleep(0.05)
        pos = st["movement"].get("pos") or before
        if list(pos[:2]) == list(before[:2]) and not (len(pos) > 3 and len(before) > 3 and pos[3] != before[3]):
            # denied: a teleporter may move us right after the deny (agent_link.DENY_TELEPORT_GRACE_S)
            end = time.monotonic() + DENY_TELEPORT_GRACE_S
            while time.monotonic() < end and list(pos[:2]) == list(before[:2]):
                time.sleep(0.05)
                pos = stc.state()["movement"].get("pos") or before
        if list(pos[:2]) != list(before[:2]):
            moved += 1
            if nav.chebyshev(tuple(before[:2]), tuple(pos[:2])) > 1:
                outcomes.append("teleported")
                stop = "teleported"
                break
            outcomes.append("moved")
        elif len(pos) > 3 and len(before) > 3 and pos[3] != before[3]:
            outcomes.append("turned")
        else:
            outcomes.append("blocked")
            stop = "blocked"
            break
        if moved >= n:
            break
        time.sleep(human.step_delay(a.run))
        human.after_step()
    return {"ok": moved == n, "dir": d, "dir_name": DIR_NAMES[d], "steps": n, "moved": moved,
            "outcomes": outcomes, "from": start, "to": pos, "stopped": stop}


def cmd_act(a, mem):
    heartbeat(mem)
    desc = f"act {a.name} {' '.join(a.args)}".strip()
    try:
        out = _act(a, mem)
    except CtlError as e:
        mem.chat_post("overseer", f"{desc}: refused: {e}", "action",
                      data={"cmd": "act", "act": a.name, "args": a.args, "ok": False, "error": str(e)})
        raise
    note = (f"moved {out['moved']}/{out['steps']} {out['dir_name']}"
            + (f" (stopped: {out['stopped']})" if out.get("stopped") else "")) if a.name == "walk" \
        else out.get("reply", "")
    mem.chat_post("overseer", f"{desc}: {note}", "action",
                  data={"cmd": "act", "act": a.name, "args": a.args, **out})
    return {"act": a.name, **out}


def _act(a, mem) -> dict:
    alive = running_tasks(mem)
    if alive:
        raise CtlError(f"task {alive[0]['task_id']} is running; no interleaved actions (stop it first)")
    if a.name == "goto":
        return _act_goto(a, mem)
    if a.name in ("unequip", "equip"):
        return _act_wear(a)
    if a.name in ("warmode", "attack"):
        return _act_combat(a)
    if a.name == "loot":
        return _act_loot(a)
    if a.name == "target":
        return _act_target(a)
    if a.name == "cast":
        return _act_cast(a)
    if a.name == "buy":
        return _act_buy(a, mem)
    if a.name == "use":
        return _act_use(a)
    if a.name == "drop":
        return _act_drop(a)
    pkt = None
    if a.name == "say":
        text = " ".join(a.args).strip().lower()
        if text not in SPEECH_ALLOWLIST:
            raise CtlError(f"speech not allowlisted: {' '.join(a.args)!r}; allowed: {list(SPEECH_ALLOWLIST)}")
        pkt = actions.say_unicode(text)
    elif a.name in ("dclick", "single_click", "menu"):
        if len(a.args) != 1:
            raise CtlError(f"{a.name} <serial>")
        s = _parse_serial(a.args[0])
        pkt = {"dclick": actions.dclick, "single_click": actions.single_click,
               "menu": actions.request_popup}[a.name](s)
    elif a.name == "menu_pick":
        if len(a.args) != 2:
            raise CtlError("menu_pick <serial> <entry index>")
        try:
            idx = int(a.args[1], 0)
        except ValueError:
            raise CtlError("menu_pick: entry index must be an integer")
        pkt = actions.popup_selection(_parse_serial(a.args[0]), idx)
    elif a.name == "open_door":
        if a.args:
            raise CtlError("open_door takes no arguments")
        pkt = actions.open_door()
    elif a.name == "target_cancel":
        if a.args:
            raise CtlError("target_cancel takes no arguments")
    elif a.name == "gump":
        if len(a.args) != 2:
            raise CtlError("gump <serial> <button>")
    elif a.name != "walk":
        raise CtlError(f"unknown act {a.name!r}; allowed: {list(ACTS)}")
    try:
        ctl = Control(a.control_port)
    except OSError as e:
        raise CtlError(f"proxy control port {a.control_port} unreachable: {e}")
    try:
        stc = StateConn(a.state_port)
    except OSError as e:
        ctl.close()
        raise CtlError(f"proxy state port {a.state_port} unreachable: {e}")
    try:
        if a.name == "walk":
            return _act_walk(a, ctl, stc)
        if a.name == "target_cancel":
            cur = (stc.state().get("world") or {}).get("target") or {}
            if not cur.get("active") or cur.get("cursor_id") is None:
                raise CtlError("no target cursor is up")
            pkt = actions.target_cancel(cur["cursor_id"], cur.get("target_type") or 0,
                                        cur.get("cursor_type") or 0)
        if a.name == "gump":
            pkt = gump_reply(stc.state(), a.args[0], a.args[1], a.text or ())
        mark = stc.mark()
        resp = ctl.send(pkt)
        if resp != "OK":
            return {"ok": False, "reply": resp}
        if a.name == "menu":           # wait for the server's context menu
            got = stc.wait_events(mark, lambda evs: any(e.get("ev") == "popup" for e in evs))
        else:                          # what the server answered, as the player would read it
            got = stc.wait_events(mark, lambda evs: False, timeout=1.5)
        heard = [journal_view(e) for e in got if e.get("ev") in JOURNAL_EVS]
        out = {"ok": True, "reply": resp, "heard": heard}
        if a.name == "menu":
            menu = next((h for h in heard if h["ev"] == "popup"), None)
            out["ok"] = menu is not None
            out["menu"] = menu
            if menu is None:
                out["error"] = "no context menu came back"
        return out
    finally:
        ctl.close()
        stc.close()


def _backpack(items: dict, me) -> int | None:
    return next((_serial(k) for k, v in items.items() if v.get("layer") == LAYER_BACKPACK
                 and v.get("container") is not None and _serial(v["container"]) == me), None)


def _connect(a):
    try:
        ctl = Control(a.control_port)
    except OSError as e:
        raise CtlError(f"proxy control port {a.control_port} unreachable: {e}")
    try:
        return ctl, StateConn(a.state_port)
    except OSError as e:
        ctl.close()
        raise CtlError(f"proxy state port {a.state_port} unreachable: {e}")


def _act_combat(a) -> dict:
    """warmode on|off: 0x72, the stock Tab toggle. attack <serial>: a hostile
    monster only (never a player, a player's pet, an NPC or anything with a
    human body; threats.identify + notoriety 3-6). Like the stock client (Tab,
    then double-click the target) it turns war mode on first, then sends 0x05.
    Refused below ATTACK_MIN_HP of max hits."""
    import threats
    ctl, stc = _connect(a)
    try:
        st = stc.state()
        world = st["world"]
        me = world["self"]
        if a.name == "warmode":
            if len(a.args) != 1 or a.args[0] not in ("on", "off"):
                raise CtlError("warmode on|off")
            on = a.args[0] == "on"
            resp = ctl.send(actions.war_mode(on))
            stc.intent("Ready to fight (war mode)" if on else "Standing down (peace mode)", "ready" if on else "idle")
            if resp != "OK":
                return {"ok": False, "reply": resp}
            end = time.monotonic() + EVENT_WAIT_S
            while time.monotonic() < end and bool(stc.state()["world"]["self"].get("warmode")) != on:
                time.sleep(0.1)
            now_on = bool(stc.state()["world"]["self"].get("warmode"))
            out = {"ok": now_on == on, "reply": resp, "warmode": now_on}
            if now_on != on:
                out["error"] = "the server didn't confirm the war mode change"
            return out
        if len(a.args) != 1:
            raise CtlError("attack <mobile serial>")
        serial = _parse_serial(a.args[0])
        key = f"0x{serial:08X}"
        if serial == _serial(me.get("serial")):
            raise CtlError("that is you")
        ok, why = _attackable(world, key)
        if not ok:
            raise CtlError(why)
        mob = world["mobiles"][key]
        label = (world.get("labels") or {}).get(key)
        kind, _player, _ev = threats.identify(mob, label)
        noto = mob.get("notoriety")
        hits, hits_max = me.get("hits"), me.get("hits_max")
        if hits is not None and hits_max and hits < ATTACK_MIN_HP * hits_max:
            raise CtlError(f"hits {hits}/{hits_max} are below {ATTACK_MIN_HP:.0%}: heal or get away first")
        pos = st["movement"]["pos"]
        mark = stc.mark()
        turned_on = False
        if not me.get("warmode"):
            resp = ctl.send(actions.war_mode(True))
            if resp != "OK":
                return {"ok": False, "reply": resp}
            turned_on = True
            Human(a.human, seed=a.seed).wait("use")
        resp = ctl.send(actions.attack(serial))
        stc.intent(f"Attacking {label or mob.get('name') or key}", "attack", (mob["x"], mob["y"]), serial)
        got = stc.wait_events(mark, lambda evs: False, timeout=1.5)
        return {"ok": resp == "OK", "reply": resp, "warmode_turned_on": turned_on,
                "target": {"serial": key, "name": label or mob.get("name"), "kind": kind, "notoriety": noto,
                           "hits": [mob.get("hits"), mob.get("hits_max")],
                           "dist": nav.chebyshev(tuple(pos[:2]), (mob["x"], mob["y"])) if pos else None},
                "heard": [journal_view(e) for e in got if e.get("ev") in JOURNAL_EVS]}
    finally:
        ctl.close()
        stc.close()


def _act_loot(a) -> dict:
    """loot <corpse serial>: open a monster's corpse within LOOT_RANGE tiles
    (0x06 double click) and move what's in it into your backpack, one item
    at a time (0x07 lift, pause, 0x08 drop), gold first, at most
    --max-items, stopping at your weight limit. Refuses corpses with a human
    body (players, human NPCs, your own: policy, no corpse runs)."""
    import threats
    if len(a.args) != 1:
        raise CtlError("loot <corpse serial>")
    serial = _parse_serial(a.args[0])
    key = f"0x{serial:08X}"
    ctl, stc = _connect(a)
    try:
        st = stc.state()
        world, pos = st["world"], st["movement"]["pos"]
        me_serial = st["movement"].get("self_serial")
        corpse = world["items"].get(key)
        if corpse is None or corpse.get("graphic") != CORPSE_GRAPHIC or corpse.get("container") is not None:
            raise CtlError(f"{key} isn't a corpse on the ground")
        body = corpse.get("amount")                       # a corpse's amount is the body it was
        name = corpse.get("name") or ""
        if body in threats.HUMAN_BODIES or "remains of" in name.lower():
            raise CtlError(f"{key} ({name or f'body 0x{body:X}'}) is a human corpse (a player, a human NPC or "
                           "you): not looted (criminal, and policy: no corpse runs)")
        dist = nav.chebyshev(tuple(pos[:2]), (corpse["x"], corpse["y"]))
        if dist > LOOT_RANGE:
            raise CtlError(f"the corpse is {dist} tiles away; walk within {LOOT_RANGE} first "
                           f"(goto {key} --range 1)")
        pack = _backpack(world["items"], me_serial)
        if pack is None:
            raise CtlError("backpack not known to the world model")
        noto_before = world["self"].get("notoriety")
        mark = stc.mark()
        human = Human(a.human, seed=a.seed)
        stc.intent(f"Looting {name or 'a corpse'}", "loot", (corpse["x"], corpse["y"]), serial)
        resp = ctl.send(actions.dclick(serial))
        if resp != "OK":
            return {"ok": False, "reply": resp}

        def contents():
            items = stc.state()["world"]["items"]
            return {k: v for k, v in items.items()
                    if v.get("container") is not None and _serial(v["container"]) == serial}
        end = time.monotonic() + EVENT_WAIT_S
        inside = contents()
        while not inside and time.monotonic() < end:
            time.sleep(0.1)
            inside = contents()
        order = sorted(inside.items(), key=lambda kv: (kv[1].get("graphic") != GOLD_GRAPHIC, kv[0]))
        taken, failed, stopped = [], [], None
        for k, it in order[:a.max_items]:
            me = stc.state()["world"]["self"]
            if me.get("weight") is not None and me.get("weight_max") and me["weight"] >= me["weight_max"]:
                stopped = f"weight {me['weight']}/{me['weight_max']}"
                break
            human.wait("drag")
            s = _serial(k)
            if ctl.send(actions.lift(s, it.get("amount") or 1)) != "OK" \
                    or ctl.send(actions.drop(s, DROP_AUTO, DROP_AUTO, 0, 0, pack)) != "OK":
                failed.append(k)
                continue
            end = time.monotonic() + EVENT_WAIT_S
            moved = False
            while time.monotonic() < end and not moved:
                v = stc.state()["world"]["items"].get(k)
                # a stack dropped onto a pile of the same kind (gold) merges: the lifted serial is deleted
                moved = v is None or (v.get("container") is not None and _serial(v["container"]) == pack)
                if not moved:
                    time.sleep(0.1)
            row = {"serial": k, "graphic": None if it.get("graphic") is None else f"0x{it['graphic']:04X}",
                   "name": it.get("name") or _tile_name(it.get("graphic")), "amount": it.get("amount")}
            (taken if moved else failed).append(row if moved else k)
        got = stc.wait_events(mark, lambda evs: False, timeout=0.5)
        noto_after = stc.state()["world"]["self"].get("notoriety")
        out = {"ok": not failed, "corpse": {"serial": key, "name": name or None,
                                            "body": None if body is None else f"0x{body:04X}", "dist": dist},
               "taken": taken, "failed": failed, "left": max(0, len(inside) - len(taken)),
               "heard": [journal_view(e) for e in got if e.get("ev") in JOURNAL_EVS]}
        if stopped:
            out["stopped"] = stopped
        if noto_after != noto_before:
            out["warning"] = (f"your notoriety changed {noto_before} -> {noto_after} "
                              f"({NOTORIETY.get(noto_after, '?')}) after looting")
        if failed:
            out["error"] = "some items didn't move (check journal/status; one may be on the cursor)"
        return out
    finally:
        ctl.close()
        stc.close()


def _attackable(world: dict, key: str) -> tuple[bool, str]:
    """(ok, why not): the monsters-only rule shared by attack and target."""
    import threats
    mob = world["mobiles"].get(key)
    if mob is None or mob.get("x") is None:
        return False, f"mobile {key} not known to the world model"
    label = (world.get("labels") or {}).get(key)
    kind, player, evidence = threats.identify(mob, label)
    if kind != "monster" or player:
        return False, (f"{key} ({label or mob.get('name')}) is not a hostile monster ({kind}; "
                       f"{', '.join(evidence)}): only monsters, never players or NPCs")
    noto = mob.get("notoriety")
    if noto not in ATTACKABLE_NOTORIETY:
        return False, (f"{key} has notoriety {noto} ({NOTORIETY.get(noto, '?')}): likely someone's pet or a "
                       f"protected creature")
    return True, ""


def _act_target(a) -> dict:
    """target self | target <serial>: answer the target cursor that is up now
    (after a spell or using a bandage/potion) with 0x6C on an entity. Allowed
    targets: yourself, a hostile monster (same rule as attack), or an item in
    your backpack. Never a player, their pet or an NPC."""
    if len(a.args) != 1:
        raise CtlError("target self | target <serial>")
    ctl, stc = _connect(a)
    try:
        st = stc.state()
        world = st["world"]
        cur = world.get("target") or {}
        if not cur.get("active") or cur.get("cursor_id") is None:
            raise CtlError("no target cursor is up (cast or use something first)")
        me_serial = st["movement"].get("self_serial")
        if a.args[0].lower() == "self":
            pos = st["movement"]["pos"]
            serial, x, y, z = me_serial, pos[0], pos[1], pos[2]
            graphic = world["self"].get("body") or 0
            what = "yourself"
        else:
            serial = _parse_serial(a.args[0])
            key = f"0x{serial:08X}"
            if serial == me_serial:
                raise CtlError("use `target self`")
            it = world["items"].get(key)
            if it is not None:
                pack = _backpack(world["items"], me_serial)
                c, depth = it.get("container"), 0
                while c is not None and _serial(c) != pack and depth < 8:
                    c, depth = (world["items"].get(f"0x{_serial(c):08X}") or {}).get("container"), depth + 1
                if pack is None or c is None or _serial(c) != pack:
                    raise CtlError(f"{key} isn't in your backpack")
                # the client sends a contained item's container-local x/y and z (actions.target_object)
                x, y, z = it.get("x") or 0, it.get("y") or 0, it.get("z") or 0
                graphic, what = it.get("graphic") or 0, it.get("name") or _tile_name(it.get("graphic"))
            else:
                ok, why = _attackable(world, key)
                if not ok:
                    raise CtlError(why)
                m = world["mobiles"][key]
                x, y, z, graphic = m["x"], m["y"], m.get("z") or 0, m.get("graphic") or 0
                what = (world.get("labels") or {}).get(key) or m.get("name")
        mark = stc.mark()
        resp = ctl.send(actions.target_object(cur["cursor_id"], serial, x, y, z, graphic,
                                              cur.get("cursor_type") or 0))
        on_map = a.args[0].lower() == "self" or f"0x{serial:08X}" in world["mobiles"]
        stc.intent(f"Targeting {what}", "target", (x, y) if on_map else None,
                   serial if on_map and a.args[0].lower() != "self" else None)
        got = stc.wait_events(mark, lambda evs: False, timeout=1.5)
        return {"ok": resp == "OK", "reply": resp, "targeted": what,
                "heard": [journal_view(e) for e in got if e.get("ev") in JOURNAL_EVS]}
    finally:
        ctl.close()
        stc.close()


def spell_id(text: str) -> int:
    """A Magery spell by number (1-64) or name (case and spacing ignored)."""
    t = text.strip()
    if t.isdigit() and 1 <= int(t) <= len(MAGERY_SPELLS):
        return int(t)
    key = "".join(t.lower().split()).replace("_", "")
    for i, name in enumerate(MAGERY_SPELLS, 1):
        if "".join(name.lower().split()) == key:
            return i
    raise CtlError(f"unknown spell {text!r}; Magery spells: {', '.join(MAGERY_SPELLS)}")


def _act_cast(a) -> dict:
    """cast <spell name or 1-64>: the Outlands client's cast request (0xFF sub
    4). Waits for the spell's target cursor, if it has one; answer it with
    `target self|<serial>`."""
    if not a.args:
        raise CtlError("cast <spell name or number>")
    sid = spell_id(" ".join(a.args))
    ctl, stc = _connect(a)
    try:
        mark = stc.mark()
        stc.intent(f"Casting {MAGERY_SPELLS[sid - 1]}", "cast")
        resp = ctl.send(actions.cast_spell(sid))
        if resp != "OK":
            return {"ok": False, "reply": resp}
        got = stc.wait_events(mark, lambda evs: any(e.get("ev") in ("target", "cliloc") for e in evs),
                              timeout=CAST_CURSOR_WAIT_S)
        cur = stc.state()["world"].get("target") or {}
        return {"ok": True, "reply": resp, "spell": MAGERY_SPELLS[sid - 1], "spell_id": sid,
                "cursor": bool(cur.get("active")), "target_type": cur.get("cursor_type"),
                "heard": [journal_view(e) for e in got if e.get("ev") in JOURNAL_EVS]}
    finally:
        ctl.close()
        stc.close()


def _spent_today(mem) -> int:
    lt = time.localtime()
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    return sum(int(e["data"].get("total") or 0) for e in mem.job_events("gold", since=midnight)
               if e["kind"] == "spend")


def _daily_cap() -> int | None:
    try:
        with open(POLICY_PATH, encoding="utf-8") as f:
            gold = json.load(f).get("gold") or {}
    except (OSError, ValueError):
        return None
    return gold.get("daily_cap_gp") if gold.get("may_spend") else 0


def _in_tree(items: dict, key: str, root: int) -> bool:
    """Is item `key` the container `root` or inside it (any bag depth)?"""
    s, depth = _serial(key), 0
    while s is not None and depth < 10:
        if s == root:
            return True
        s = (items.get(f"0x{s:08X}") or {}).get("container")
        s = _serial(s) if s is not None else None
        depth += 1
    return False


def _where(items: dict, key: str, me) -> str:
    """A readable name for where an item or container is: your backpack, your
    bank box, a corpse, the ground, or another container's serial."""
    pack, s, depth = _backpack(items, me), _serial(key), 0
    while s is not None and depth < 10:
        v = items.get(f"0x{s:08X}") or {}
        if s == pack:
            return "backpack"
        if v.get("layer") == LAYER_BANK:
            return "bank"
        if v.get("graphic") == CORPSE_GRAPHIC:
            return "corpse"
        c = v.get("container")
        if c is None:
            return "ground container" if v.get("x") is not None else f"0x{s:08X}"
        s, depth = _serial(c), depth + 1
    return key


def _act_drop(a) -> dict:
    """drop <item serial> <container serial> [--amount N]: move an item into a
    container, from anywhere the world model knows it (your backpack, the open
    bank box, a chest, a corpse, the ground...) into any container (user
    decision 2026-09-30: no harness-side container limits; the server decides
    what you may take or reach). 0x07 lift (N of a stack), human pause, 0x08
    drop into the container (auto-position), like dragging it in the client."""
    if len(a.args) != 2:
        raise CtlError("drop <item serial> <container serial> [--amount N]")
    item_s, cont_s = _parse_serial(a.args[0]), _parse_serial(a.args[1])
    ikey, ckey = f"0x{item_s:08X}", f"0x{cont_s:08X}"
    if item_s == cont_s:
        raise CtlError("can't drop a container into itself")
    ctl, stc = _connect(a)
    try:
        st = stc.state()
        items, me = st["world"]["items"], st["movement"].get("self_serial")
        it = items.get(ikey)
        if it is None:
            raise CtlError(f"item {ikey} not known to the world model")
        cont = items.get(ckey)
        if cont is None:
            raise CtlError(f"container {ckey} not known to the world model (open it first)")
        if _in_tree(items, ckey, item_s):
            raise CtlError("can't drop a container into itself")
        have = it.get("amount") or 1
        amount = have if a.amount is None else a.amount
        if not 1 <= amount <= have:
            raise CtlError(f"--amount must be 1..{have}")
        name = item_label(it)
        src, where = _where(items, ikey, me), _where(items, ckey, me)
        stc.intent(f"Moving {amount} {name or ikey} from the {src} to the {where}", "store")
        mark = stc.mark()
        resp = ctl.send(actions.lift(item_s, amount))
        if resp != "OK":
            return {"ok": False, "reply": resp}
        Human(a.human, seed=a.seed).wait("drag")
        resp = ctl.send(actions.drop(item_s, DROP_AUTO, DROP_AUTO, 0, 0, cont_s))
        if resp != "OK":
            return {"ok": False, "reply": resp,
                    "error": "lifted but the drop was refused; the item may be on the cursor"}

        def moved():
            v = stc.state()["world"]["items"].get(ikey)
            if v is None:                          # merged into a stack there (gold)
                return True
            if amount < have:                      # a partial lift leaves the rest behind
                return (v.get("amount") or 1) == have - amount
            return v.get("container") is not None and _serial(v["container"]) == cont_s
        end = time.monotonic() + EVENT_WAIT_S
        ok = moved()
        while not ok and time.monotonic() < end:
            time.sleep(0.1)
            ok = moved()
        got = stc.wait_events(mark, lambda evs: False, timeout=0.5)
        out = {"ok": ok, "reply": resp, "moved": ok, "item": name, "amount": amount, "from": src, "into": where,
               "heard": [journal_view(e) for e in got if e.get("ev") in JOURNAL_EVS]}
        if not ok:
            out["error"] = "the world model doesn't show the item moved (check journal/status)"
        return out
    finally:
        ctl.close()
        stc.close()


def _act_use(a) -> dict:
    """use <name words | 0xGRAPHIC>: double-click the matching item in your
    backpack (drink a potion, apply a bandage: then `target self`). Among
    several stacks of the same thing, the smallest goes first."""
    if not a.args:
        raise CtlError("use <item name words | 0xGRAPHIC>")
    want = " ".join(a.args).strip().lower()
    ctl, stc = _connect(a)
    try:
        st = stc.state()
        items = st["world"]["items"]
        pack = _backpack(items, st["movement"].get("self_serial"))
        if pack is None:
            raise CtlError("backpack not known to the world model")
        cands = []
        for k, it in _pack_items(items, pack):
            name = (item_label(it) or "").lower()
            if want.startswith("0x"):
                ok = f"0x{it['graphic']:04x}" == want
            else:
                ok = all(w in name for w in want.split())
            if ok:
                cands.append((k, it, name))
        if not cands:
            raise CtlError(f"nothing matching {want!r} in your backpack")
        kinds = {(it["graphic"], it.get("hue")) for _, it, _ in cands}
        if len(kinds) > 1:
            raise CtlError(f"{want!r} matches different items: {sorted({n for _, _, n in cands})}; be more specific "
                           f"or use the graphic")
        k, it, name = min(cands, key=lambda c: (c[1].get("amount") or 1, c[0]))
        mark = stc.mark()
        stc.intent(f"Using {name or k}", "use")
        resp = ctl.send(actions.dclick(_serial(k)))
        got = stc.wait_events(mark, lambda evs: any(e.get("ev") == "target" for e in evs), timeout=1.5)
        cur = stc.state()["world"].get("target") or {}
        return {"ok": resp == "OK", "reply": resp, "used": {"serial": k, "name": name,
                                                             "graphic": f"0x{it['graphic']:04X}",
                                                             "amount": it.get("amount") or 1},
                "cursor": bool(cur.get("active")),
                "heard": [journal_view(e) for e in got if e.get("ev") in JOURNAL_EVS]}
    finally:
        ctl.close()
        stc.close()


def _act_buy(a, mem) -> dict:
    """buy <vendor serial> [ITEM WORDS…] [--amount N]: opens the vendor's Buy
    list through the context menu (like the stock client) and, when an item
    is named, sends the 0x3B buy request for it, checking your gold and the
    policy's daily cap (harness/data/policy.json, spends recorded as job
    events `gold/spend`). Without an item it only returns the price list.
    The price list maps to the vendor container's items in reverse order
    (ClassicUO BuyList; pinned by the capture 20260928_164548 purchase)."""
    if not a.args:
        raise CtlError("buy <vendor serial> [item words...] [--amount N]")
    vendor = _parse_serial(a.args[0])
    vkey = f"0x{vendor:08X}"
    want = " ".join(a.args[1:]).strip().lower()
    amount = a.amount or 1
    if amount < 1:
        raise CtlError("--amount must be >= 1")
    ctl, stc = _connect(a)
    try:
        st = stc.state()
        world, pos = st["world"], st["movement"]["pos"]
        mob = world["mobiles"].get(vkey)
        if mob is None or mob.get("x") is None:
            raise CtlError(f"vendor {vkey} not known to the world model")
        dist = nav.chebyshev(tuple(pos[:2]), (mob["x"], mob["y"]))
        if dist > VENDOR_RANGE:
            raise CtlError(f"the vendor is {dist} tiles away; get within {VENDOR_RANGE} (goto {vkey})")
        human = Human(a.human, seed=a.seed)
        mark = stc.mark()
        vname = (world.get("labels") or {}).get(vkey) or mob.get("name") or vkey
        stc.intent(f"Buying {want} from {vname}" if want else f"Browsing {vname}'s wares", "buy",
                   (mob["x"], mob["y"]), vendor)
        if ctl.send(actions.request_popup(vendor)) != "OK":
            raise CtlError("context menu request refused")
        evs = stc.wait_events(mark, lambda e: any(x.get("ev") == "popup" for x in e))
        menu = next((e for e in evs if e.get("ev") == "popup" and _serial(e.get("serial")) == vendor), None)
        entry = next((x for x in (menu or {}).get("entries") or [] if x.get("cliloc") == BUY_CLILOC), None)
        if entry is None:
            raise CtlError("no Buy entry in the vendor's context menu")
        human.wait("menu")
        mark = stc.mark()
        if ctl.send(actions.popup_selection(vendor, entry["index"])) != "OK":
            raise CtlError("menu selection refused")
        evs = stc.wait_events(mark, lambda e: any(x.get("ev") == "buy_list" for x in e))
        bl = next((e for e in evs if e.get("ev") == "buy_list"), None)
        if bl is None:
            raise CtlError("the vendor sent no price list")
        items = stc.state()["world"]["items"]
        cont = _serial(bl["container"])
        # the container's display order = its latest 0x3C, which the vendor sends right before the list
        order = None
        for e in evs:
            if e.get("ev") == "buy_list":
                break
            for c, serials in (e.get("containers") or []) if e.get("ev") == "container_content" else []:
                if _serial(c) == cont:
                    order = [f"0x{_serial(s):08X}" for s in serials]
        inside = [(k, items.get(k) or {}) for k in order] if order is not None else []
        cinfo = items.get(f"0x{cont:08X}") or {}
        if cinfo.get("graphic") == SORTED_BUY_CONTAINER:
            inside.sort(key=lambda kv: kv[1].get("x") or 0)
        else:
            inside.reverse()
        offers = []
        for i, row in enumerate(bl.get("items") or []):
            k, v = inside[i] if i < len(inside) else (None, {})
            offers.append({"name": item_name(row.get("name")), "price": row.get("price"), "serial": k,
                           "stock": v.get("amount"), "graphic": None if v.get("graphic") is None
                           else f"0x{v['graphic']:04X}"})
        mapped = order is not None and len(inside) == len(bl.get("items") or [])
        if not want:
            out = {"ok": True, "vendor": vkey, "list": offers,
                   "note": "nothing bought; the shop window stays open for the user to close"}
            if not mapped:
                out["warning"] = "no matching container packet before the list: item serials/stock unknown"
            return out
        if not mapped:
            raise CtlError("can't match the price list to the vendor's items (no container packet with the same "
                           f"count before the list: {0 if order is None else len(order)} items vs "
                           f"{len(bl.get('items') or [])} prices); not buying on a guess")
        hits = [o for o in offers if o["serial"] and all(w in o["name"].lower() for w in want.split())]
        exact = [o for o in hits if o["name"].lower() == want]
        if not hits:
            raise CtlError(f"no {want!r} in the list: {[o['name'] for o in offers]}")
        if len(hits) > 1 and len(exact) != 1:
            raise CtlError(f"{want!r} is ambiguous: {[o['name'] for o in hits]}")
        offer = exact[0] if exact else hits[0]
        if offer["stock"] is not None and amount > offer["stock"]:
            raise CtlError(f"the vendor has only {offer['stock']} {offer['name']}")
        total = offer["price"] * amount
        me = stc.state()["world"]["self"]
        gold_before = me.get("gold")
        if gold_before is not None and total > gold_before:
            raise CtlError(f"{amount} {offer['name']} cost {total} gp; you have {gold_before}")
        cap, spent = _daily_cap(), _spent_today(mem)
        if cap is not None and spent + total > cap:
            raise CtlError(f"daily gold cap: {spent} spent today + {total} > {cap} (harness/data/policy.json)")
        human.wait("menu")
        mark = stc.mark()
        resp = ctl.send(actions.buy_request(vendor, [(_serial(offer["serial"]), amount)]))
        if resp != "OK":
            return {"ok": False, "reply": resp}
        end = time.monotonic() + EVENT_WAIT_S
        gold_after = gold_before
        while time.monotonic() < end:
            gold_after = stc.state()["world"]["self"].get("gold")
            if gold_before is None or gold_after != gold_before:
                break
            time.sleep(0.1)
        got = stc.wait_events(mark, lambda evs: False, timeout=0.5)
        paid = (gold_before - gold_after) if gold_before is not None and gold_after is not None else None
        out = {"ok": paid is None or paid > 0, "reply": resp, "vendor": vkey, "bought": offer["name"],
               "amount": amount, "price": offer["price"], "total": total, "paid": paid,
               "gold": gold_after, "spent_today": spent + (paid if paid and paid > 0 else 0), "daily_cap": cap,
               "heard": [journal_view(e) for e in got if e.get("ev") in JOURNAL_EVS]}
        if paid is not None and paid > 0:
            mem.job_event("gold", "spend", {"vendor": vkey, "item": offer["name"], "amount": amount,
                                            "price": offer["price"], "total": paid})
        elif paid is not None:
            out["error"] = "your gold didn't change: the purchase probably failed (see heard)"
        return out
    finally:
        ctl.close()
        stc.close()


def _act_wear(a) -> dict:
    """unequip <serial>: an item you wear -> your backpack (0x07 lift, pause,
    0x08 drop into the pack). equip <serial>: an item in your backpack (any
    bag depth) -> worn on its tiledata layer (0x07 lift, pause, 0x13 equip
    request). The stock client's drag sequences; waits for the world model to
    show the move."""
    if len(a.args) != 1:
        raise CtlError(f"{a.name} <item serial>")
    serial = _parse_serial(a.args[0])
    key = f"0x{serial:08X}"
    try:
        ctl = Control(a.control_port)
    except OSError as e:
        raise CtlError(f"proxy control port {a.control_port} unreachable: {e}")
    try:
        stc = StateConn(a.state_port)
    except OSError as e:
        ctl.close()
        raise CtlError(f"proxy state port {a.state_port} unreachable: {e}")
    try:
        st = stc.state()
        me = st["movement"].get("self_serial")
        items = st["world"]["items"]
        it = items.get(key)
        if it is None:
            raise CtlError(f"item {key} not known to the world model")
        pack = _backpack(items, me)
        if pack is None:
            raise CtlError("backpack not known to the world model")
        worn = it.get("container") is not None and _serial(it["container"]) == me and bool(it.get("layer"))
        if a.name == "unequip":
            if not worn:
                raise CtlError(f"{key} isn't worn by you")
            if it.get("layer") == LAYER_BACKPACK:
                raise CtlError("that is your backpack")
            second = actions.drop(serial, DROP_AUTO, DROP_AUTO, 0, 0, pack)
            target = pack
        else:
            if worn:
                raise CtlError(f"{key} is already worn")
            c, depth = it.get("container"), 0
            while c is not None and _serial(c) != pack and depth < 8:
                c, depth = (items.get(f"0x{_serial(c):08X}") or {}).get("container"), depth + 1
            if c is None or _serial(c) != pack:
                raise CtlError(f"{key} isn't in your backpack")
            layer = _tile_layer(it.get("graphic"))
            if not layer:
                raise CtlError(f"{key} (graphic {it.get('graphic')}) has no wearable layer in tiledata")
            second = actions.equip_request(serial, layer, me)
            target = me

        def done():
            v = stc.state()["world"]["items"].get(key)
            return v is not None and v.get("container") is not None and _serial(v["container"]) == target
        mark = stc.mark()
        resp = ctl.send(actions.lift(serial, it.get("amount") or 1))
        if resp != "OK":
            return {"ok": False, "reply": resp}
        Human(a.human, seed=a.seed).wait("drag")
        resp = ctl.send(second)
        if resp != "OK":
            return {"ok": False, "reply": resp,
                    "error": "lifted but the second packet was refused; the item may be on the cursor"}
        end = time.monotonic() + EVENT_WAIT_S
        while time.monotonic() < end and not done():
            time.sleep(0.1)
        moved = done()
        got = stc.wait_events(mark, lambda evs: False, timeout=0.5)
        out = {"ok": moved, "reply": resp, "moved": moved,
               "heard": [journal_view(e) for e in got if e.get("ev") in JOURNAL_EVS]}
        if not moved:
            out["error"] = "the world model doesn't show the item moved (check journal/status)"
        return out
    finally:
        ctl.close()
        stc.close()


def _tile_layer(graphic):
    """Tiledata layer of an item graphic (install dir, read-only), or None."""
    if graphic is None:
        return None
    try:
        import uomap
        it = uomap.tiledata().item(graphic)
    except (OSError, ValueError):
        return None
    return it.layer if it else None


def gump_reply(state: dict, serial_arg: str, button_arg: str, texts=()) -> bytes:
    """A 0xB1 reply the overseer may send, or CtlError. Like the stock client it
    carries every text entry (current text, or the overseer's --text ID=VALUE)
    and the switches that start checked. Refuses: the captcha (human-only),
    gumps without reply buttons (decoys: any reply flags a bot), buttons the
    layout doesn't offer, closing (0) a noclose gump, anything but closing on a
    gump that mentions renouncing Young status, and text for an entry the gump
    doesn't have or longer than its limit."""
    serial = _parse_serial(serial_arg)
    try:
        button = int(button_arg, 0)
    except ValueError:
        raise CtlError("gump: button must be an integer")
    gumps = [g for g in (state.get("world") or {}).get("gumps") or []
             if g.get("open") and _serial(g.get("serial")) == serial]
    if not gumps:
        raise CtlError(f"no open gump with serial 0x{serial:08X}")
    g = gumps[-1]
    view = gump_view(g)
    if _serial(g.get("gump_id")) == CAPTCHA_GUMP_ID:
        raise CtlError("that is the captcha: only the human answers it (ANTICHEAT.md §8.8)")
    if not view["buttons"]:
        raise CtlError("gump has no reply buttons (decoy/honeypot shape, ANTICHEAT.md §8.13): never reply")
    if any(w in t.lower() for t in view["texts"] for w in RENOUNCE_WORDS) and button != 0:
        raise CtlError("gump mentions renouncing Young status: only closing it (button 0) is allowed; "
                       "leaving Shelter is the human's decision")
    if button == 0 and not view["closable"]:
        raise CtlError("gump is noclose; button 0 isn't available")
    if button != 0 and button not in view["buttons"]:
        raise CtlError(f"button {button} not in the gump's reply buttons {view['buttons']}")
    entries, switches = gump_reply_fields(g.get("layout") or "", g.get("lines") or [])
    limits = {e["id"]: e.get("limit") for e in view["controls"]["entries"]}
    overrides = {}
    for spec in texts or ():
        eid, sep, value = spec.partition("=")
        try:
            eid = int(eid, 0)
        except ValueError:
            eid = None
        if not sep or eid is None:
            raise CtlError(f"--text needs ID=VALUE, got {spec!r}")
        if eid not in limits:
            raise CtlError(f"text entry {eid} not in the gump's entries {sorted(limits)}")
        lim = limits[eid]
        if lim and len(value) > lim:
            raise CtlError(f"text for entry {eid} is {len(value)} chars; its limit is {lim}")
        if len(value) > GUMP_TEXT_MAX or any(ord(c) < 32 for c in value):
            raise CtlError(f"text for entry {eid}: printable text up to {GUMP_TEXT_MAX} chars only")
        overrides[eid] = value
    return actions.gump_response(serial, _serial(g.get("gump_id")), button, switches=switches,
                                 text_entries=[(eid, overrides.get(eid, v)) for eid, v in entries])


def _act_goto(a, mem) -> dict:
    """Walk with the Mover (map pathfinding, doors, shoving, human pacing) to a
    tile (`goto x y [--z Z]`), a mobile (`goto 0xSERIAL`: follows it and stays
    within one storey of it) or a ground item (`goto 0xSERIAL`: its tile or next
    to it, on its level). --z Z: arrive standing within 10 of Z (the hill, not
    the cave under it)."""
    import contextlib
    import agent_link
    usage = "goto <x> <y> [--z Z] | goto <mobile or ground item serial>"
    if len(a.args) == 1:
        target = _parse_serial(a.args[0])
        key = f"0x{target:08X}"
    elif len(a.args) == 2:
        try:
            target = (int(a.args[0]), int(a.args[1]))
        except ValueError:
            raise CtlError(usage)
    else:
        raise CtlError(usage)
    level = lambda z0: (lambda z: abs(z - z0) <= GOTO_Z_TOL)  # noqa: E731
    with contextlib.redirect_stdout(sys.stderr):          # stdout is the one JSON reply
        try:
            link = agent_link.Link(a.control_port, a.state_port)
        except OSError as e:
            raise CtlError(f"proxy unreachable: {e}")
        mover = agent_link.Mover(link, mem, Human(a.human, seed=a.seed), max_blocked=20, doors=True,
                                 use_map=not a.no_map)
        z_ok = None if a.z is None else level(a.z)
        if isinstance(target, int):
            world = link.state()["world"]
            mob = world["mobiles"].get(key)
            item = world["items"].get(key)
            if mob and mob.get("x") is not None:
                radius = 2 if a.range is None else a.range
                if a.z is None and mob.get("z") is not None:
                    z_ok = agent_link.same_floor(mob["z"])

                def center():
                    m = link.state()["world"]["mobiles"].get(key) or mob
                    return (m["x"], m["y"])
                label, text = f"to {key}", f"Walking to {mob.get('name') or key}"
            elif item and item.get("container") is None and item.get("x") is not None:
                radius = 0 if a.range is None else a.range
                if a.z is None and item.get("z") is not None:
                    z_ok = level(item["z"])
                spot = (item["x"], item["y"])

                def center():
                    return spot
                label, text = f"to {key}", f"Walking to {item.get('name') or key}"
            else:
                raise CtlError(f"{key} is neither a mobile nor a ground item the world model knows")
        else:
            radius = 0 if a.range is None else a.range

            def center():
                return target
            label, text = f"to {target[0]},{target[1]}", f"Walking to {target[0]},{target[1]}"
        link.intent(text, "goto", center(), loop="overseer", target_serial=key if isinstance(target, int) else None)
        start = link.pos()
        try:
            mover.walk_to(center, radius, label, max_moves=a.max_moves, z_ok=z_ok)
            ok, err = True, None
        except agent_link.Abort as e:
            ok, err = False, str(e)
        end = link.pos()
        where = text.removeprefix("Walking to ")
        link.intent(f"Arrived at {where}" if ok else f"Stopped walking to {where}: {err}"[:200],
                    "arrived" if ok else "stopped", end[:2], loop="overseer")
    out = {"ok": ok, "from": start, "to": end, "steps": mover.steps, "blocked": mover.blocked_count,
           "doors_opened": mover.doors_opened,
           "reply": f"{'arrived' if ok else 'stopped'} at {end[0]},{end[1]} after {mover.steps} steps"}
    if err:
        out["error"] = err
    return out


def cmd_map(a, mem):
    """The local map around the player (localmap.render): levels, what is
    reachable, doors, trees, ground items, mobiles; with --to, the planned
    route there."""
    import localmap
    import pathfind
    import uomap
    try:
        stc = StateConn(a.state_port)
    except OSError as e:
        raise CtlError(f"proxy state port {a.state_port} unreachable: {e}")
    try:
        st = stc.state()
    finally:
        stc.close()
    if not st["movement"].get("pos"):
        raise CtlError("position unknown (not logged in?)")
    facet = (st["world"].get("self") or {}).get("map")
    walk = pathfind.Walkers().get(facet, (
        (it.get("x"), it.get("y"), it.get("graphic"), it.get("z"))
        for it in st["world"]["items"].values() if it.get("container") is None))
    if walk is None:
        raise CtlError(f"no map geometry for facet {facet} (rental rooms are blank; use status/journal)")
    to = None
    if a.to:
        to = (a.to[0], a.to[1])
    return localmap.render(st, walk, radius=max(3, min(a.radius, 30)), to=to, to_z=a.z,
                           to_range=a.range or 0, umap=uomap.UoMap(0 if facet is None else facet))


def _knowledge(mem):
    import knowledge
    return knowledge, knowledge.Knowledge(mem.con)


def _situation(a, mem) -> dict:
    """The current situation for knowledge recall: position, nearby NPCs,
    open junctures, intent, running task (proxy parts only when it answers)."""
    sit = {"junctures": [f"{j['kind']} {j['summary']}" for j in mem.junctures(open_only=True, limit=10)],
           "task": " ".join(t["task"] for t in running_tasks(mem))}
    try:
        resp = state_query(a.state_port)
    except (OSError, ValueError):
        return sit
    if resp.get("ok"):
        s = summarize(resp)
        sit.update(pos=s["pos"], facet=s["facet"],
                   mobiles=[m["label"] or m["name"] for m in s["mobiles"][:10] if m["label"] or m["name"]],
                   intent=(s.get("intent") or {}).get("text"))
    return sit


MEMORY_ROW_RESULTS = 12              # entries per memory row in the chat (the viz folds them)


def _compact(e: dict) -> dict:
    """A knowledge entry as shown in a chat memory row (content cut to 300)."""
    out = {k: e.get(k) for k in ("id", "kind", "topic", "confidence", "importance") if k in e}
    out["content"] = (e.get("content") or "")[:300]
    for k in ("score", "status", "similarity"):
        if e.get(k) is not None:
            out[k] = e[k]
    return out


def cmd_know(a, mem):
    """The overseer's long-term memory (harness/knowledge.py; docs/MEMORY.md).
    Every operation but `stats` posts one chat row of kind `memory` so the viz
    shows lookups with their results and writes with what changed:
    data = {cmd: "know", op, ...} with, by op, `query`, `results`, `relevant`,
    `standing`, `related`, `entry`, `id`, `action`, `reason`, `counts`."""
    kmod, k = _knowledge(mem)
    heartbeat(mem)
    op = a.know_op
    row = None                                    # (text, data) of the memory chat row
    try:
        if op == "add":
            content = " ".join(a.content)
            out = k.add(a.kind, a.topic, content, tags=a.tags or (), entities=a.entity or (),
                        at=tuple(a.at) if a.at else None, source=a.source, ref=a.ref,
                        confidence=a.confidence, importance=a.importance, supersedes=a.supersedes)
            verb = {"added": "remembered", "confirmed": "confirmed", "superseded": "remembered"}[out["action"]]
            row = (f"{verb} #{out['id']} {a.kind} [{a.topic}]: {content}"
                   + (f" (supersedes #{a.supersedes})" if a.supersedes else ""),
                   {"id": out["id"], "action": out["action"], "entry": _compact(k.get(out["id"])),
                    "related": [_compact(r) for r in out["related"]], "supersedes": a.supersedes})
        elif op == "update":
            out = k.update(a.id, content=" ".join(a.content) if a.content else None, topic=a.topic,
                           tags=a.tags, at=tuple(a.at) if a.at else None, confidence=a.confidence,
                           importance=a.importance, source=a.source, ref=a.ref)
            new = out["id"] != a.id
            row = (f"updated #{a.id}" + (f" -> new version #{out['id']}" if new else ""),
                   {"id": out["id"], "action": out["action"], "entry": _compact(k.get(out["id"])),
                    "supersedes": a.id if new else None, "related": [_compact(r) for r in out.get("related", [])]})
        elif op == "confirm":
            out = k.confirm(a.id, source=a.source, ref=a.ref)
            row = (f"confirmed #{a.id} (confidence {out['confidence']}, {out['confirmations']}x)",
                   {"id": a.id, "action": "confirmed", "entry": _compact(k.get(a.id))})
        elif op == "retract":
            out = k.retract(a.id, a.reason)
            row = (f"retracted #{a.id}: {a.reason}", {"id": a.id, "action": "retracted", "reason": a.reason,
                                                       "entry": _compact(k.get(a.id))})
        elif op == "get":
            e = k.get(a.id, history=a.history)
            if e is None:
                raise CtlError(f"no knowledge entry #{a.id}")
            out = {"entry": e}
            row = (f"looked up #{a.id} [{e['topic']}]", {"id": a.id, "entry": _compact(e),
                                                          "history": e.get("history", [])})
        elif op == "search":
            near = None
            if a.near:
                near = tuple(a.near) if len(a.near) == 3 else (0, a.near[0], a.near[1])
            elif a.here:
                sit = _situation(a, mem)
                if sit.get("pos"):
                    near = (sit.get("facet") or 0, sit["pos"][0], sit["pos"][1])
            query = " ".join(a.query)
            res = k.search(query or None, kind=a.kind, tags=a.tag or (), near=near,
                           limit=a.limit, include_inactive=a.all)
            out = {"results": [kmod._brief(e) | {"status": e["status"]} for e in res]}
            filters = " ".join(f for f in (f"kind={a.kind}" if a.kind else "",
                                           *(f"tag={t}" for t in (a.tag or ())),
                                           f"near {near[1]},{near[2]}" if near else "") if f)
            row = (f"recalled '{query}'" + (f" ({filters})" if filters else "") + f": {len(res)} result(s)",
                   {"query": query, "filters": filters, "near": list(near) if near else None,
                    "results": [_compact(e) for e in res[:MEMORY_ROW_RESULTS]]})
        elif op == "brief":
            out = k.brief(_situation(a, mem), limit=a.limit)
            row = (f"briefed: {len(out['relevant'])} relevant, {len(out['standing'])} standing",
                   {"query": out["query"], "near": out["near"],
                    "relevant": [_compact(e) for e in out["relevant"][:MEMORY_ROW_RESULTS]],
                    "standing": [_compact(e) for e in out["standing"][:MEMORY_ROW_RESULTS]]})
        elif op == "review":
            out = k.review(stale_days=a.stale_days)
            counts = {"unconfirmed_inferences": len(out["unconfirmed_inferences"]), "stale": len(out["stale"]),
                      "topics_with_several_facts": len(out["topics_with_several_facts"])}
            row = ("reviewed memory: " + ", ".join(f"{n} {name.replace('_', ' ')}" for name, n in counts.items()),
                   {"counts": counts, "results": [_compact(e) for e in out["unconfirmed_inferences"][:6]]})
        else:
            out = k.stats()
    except kmod.KnowledgeError as e:
        raise CtlError(str(e))
    if row is not None:
        text, data = row
        mem.chat_post("overseer", text[:500], "memory", data={"cmd": "know", "op": op, **data})
    return {"ok": True, **out}


def cmd_intent(a, mem):
    """Tell the viz what the overseer is trying to do at a higher level
    ("Hunting mongbats for 100 gold"), or clear it (--clear). Acts report
    their own step-level intents; this is for goals between them."""
    heartbeat(mem)
    try:
        stc = StateConn(a.state_port)
    except OSError as e:
        raise CtlError(f"proxy state port {a.state_port} unreachable: {e}")
    try:
        text = None if a.clear else " ".join(a.text).strip()
        if not a.clear and not text:
            raise CtlError("intent <text…> [--kind K] [--target X Y] | intent --clear")
        ok = stc.intent(text, a.kind or "goal", tuple(a.target) if a.target else None,
                        _parse_serial(a.follow) if a.follow else None)
    finally:
        stc.close()
    if not ok:
        raise CtlError("the proxy didn't take the intent (no game session?)")
    return {"ok": True, "intent": text}


def cmd_screenshot(a, mem):
    """A PNG of the game window (harness/screen.py: Windows Graphics Capture,
    passive). Read the file to look at it."""
    import screen
    heartbeat(mem)
    try:
        return screen.screenshot(a.out, tuple(a.crop) if a.crop else None)
    except screen.ScreenError as e:
        raise CtlError(str(e))


def cmd_npcs(a, mem):
    """Every mobile the world model knows (what the viz map shows), not just the
    ones in view: search by name/title words, nearest first. Beyond the view
    range the position is where it was last seen, and NPCs wander, so `goto
    <serial>` walks there and re-checks on arrival."""
    try:
        stc = StateConn(a.state_port)
    except OSError as e:
        raise CtlError(f"proxy state port {a.state_port} unreachable: {e}")
    try:
        resp = stc.state()
    finally:
        stc.close()
    world, mv = resp.get("world") or {}, resp.get("movement") or {}
    pos, me = mv.get("pos"), mv.get("self_serial")
    labels = world.get("labels") or {}
    words = [w.lower() for w in a.words]
    rows = []
    for key, m in (world.get("mobiles") or {}).items():
        if m.get("x") is None or _serial(key) == me:
            continue
        label = labels.get(key) or m.get("name") or ""
        if words and not all(w in label.lower() for w in words):
            continue
        dist = nav.chebyshev((m["x"], m["y"]), (pos[0], pos[1])) if pos else None
        rows.append({"serial": key, "label": label or None, "name": m.get("name"),
                     "notoriety": m.get("notoriety"), "notoriety_name": NOTORIETY.get(m.get("notoriety")),
                     "x": m["x"], "y": m["y"], "z": m.get("z"), "dist": dist,
                     "in_view": dist is not None and dist <= NEARBY_RANGE})
    rows.sort(key=lambda r: (r["dist"] is None, r["dist"] or 0))
    return {"ok": True, "pos": pos, "matches": len(rows), "npcs": rows[:a.limit],
            "note": f"in_view = within {NEARBY_RANGE} tiles (live position); others are last seen there"}


def cmd_journal(a, mem):
    """Recent world events a player reads: messages, gumps, menus, vendor
    lists (the proxy's event ring), newest last."""
    try:
        stc = StateConn(a.state_port)
    except OSError as e:
        raise CtlError(f"proxy state port {a.state_port} unreachable: {e}")
    try:
        evs, _ = stc.events(0)
    finally:
        stc.close()
    rows = [{"t": e.get("t"), **journal_view(e["data"])} for e in evs
            if e.get("origin") == "world" and e["data"].get("ev") in JOURNAL_EVS]
    return {"ok": True, "journal": rows[-a.n:]}


# ---------------------------------------------------------------------- main
class JsonParser(argparse.ArgumentParser):
    """Usage errors come back as the one JSON object too."""

    def error(self, message):
        raise CtlError(f"usage: {message}")


def build_parser() -> argparse.ArgumentParser:
    ap = JsonParser(prog="ctl.py", description="overseer control CLI (docs/OVERSEER.md)")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--state-port", type=int, default=25942)
    ap.add_argument("--control-port", type=int, default=25941)
    ap.add_argument("--log-dir", default=LOG_DIR, help="task logs (default logs/tasks)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("run")
    p.add_argument("task")
    p.add_argument("args", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("stop")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--grace", type=float, default=20.0, help="seconds to let the wrapper report")
    p.set_defaults(fn=cmd_stop)
    p = sub.add_parser("wait")
    p.add_argument("--timeout", type=float, default=1800.0, help="seconds; <= 0 waits forever")
    p.add_argument("--include-info", action="store_true")
    p.add_argument("--poll", type=float, default=1.0, help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_wait)
    p = sub.add_parser("ack")
    p.add_argument("juncture_id", type=int)
    p.set_defaults(fn=cmd_ack)
    p = sub.add_parser("junctures")
    p.add_argument("--open", action="store_true")
    p.add_argument("--after", type=int, default=0)
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(fn=cmd_junctures)
    p = sub.add_parser("chat")
    p.add_argument("--after", type=int, default=0)
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--role", choices=Memory.CHAT_ROLES)
    p.set_defaults(fn=cmd_chat)
    for name, kind in (("say", "message"), ("think", "thought"), ("note-action", "action")):
        p = sub.add_parser(name)
        p.add_argument("text", nargs="+")
        p.set_defaults(fn=_post(kind))
    p = sub.add_parser("act")
    p.add_argument("name", help=f"one of {', '.join(ACTS)}")
    p.add_argument("args", nargs="*")
    p.add_argument("--run", action="store_true", help="walk: run instead of walk")
    p.add_argument("--human", choices=sorted(PROFILES), default="normal", help="walk pacing profile")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--range", type=int, default=None,
                   help="goto: stop within this many tiles (default 0 for a tile, 2 for a mobile)")
    p.add_argument("--max-moves", type=int, default=GOTO_MAX_MOVES)
    p.add_argument("--max-items", type=int, default=LOOT_MAX_ITEMS, help="loot: at most this many items")
    p.add_argument("--amount", type=int, default=None, help="buy: how many (default 1)")
    p.add_argument("--text", action="append", metavar="ID=VALUE",
                   help="gump: set text entry ID (repeatable); other entries keep their current text")
    p.add_argument("--z", type=int, default=None,
                   help="goto x y: arrive standing within 10 of this z (a hill vs the cave under it)")
    p.add_argument("--no-map", action="store_true", help=argparse.SUPPRESS)   # offline tests only
    p.set_defaults(fn=cmd_act)
    p = sub.add_parser("journal")
    p.add_argument("--n", type=int, default=30)
    p.set_defaults(fn=cmd_journal)
    p = sub.add_parser("npcs", help="search every mobile the world model knows (the viz map), nearest first")
    p.add_argument("words", nargs="*", help="name/title words, e.g. mage, 'the scribe', Sherwin")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_npcs)
    p = sub.add_parser("map", help="ASCII map around you: levels, reachability, doors, trees, items, mobiles")
    p.add_argument("--radius", type=int, default=12)
    p.add_argument("--to", type=int, nargs=2, metavar=("X", "Y"), help="also plan and draw a route there")
    p.add_argument("--z", type=int, default=None, help="with --to: arrive at this level")
    p.add_argument("--range", type=int, default=0, help="with --to: stop within this many tiles")
    p.set_defaults(fn=cmd_map)
    p = sub.add_parser("intent", help="tell the viz what you're trying to do (a goal between acts)")
    p.add_argument("text", nargs="*")
    p.add_argument("--kind", help="short phase key shown as the badge (default: goal)")
    p.add_argument("--target", type=int, nargs=2, metavar=("X", "Y"), help="a tile to mark on the map")
    p.add_argument("--follow", help="a mobile/item serial the map marker follows")
    p.add_argument("--clear", action="store_true")
    p.set_defaults(fn=cmd_intent)
    p = sub.add_parser("screenshot", help="PNG of the game window (passive capture; see harness/screen.py)")
    p.add_argument("--out", default=None, help="default logs/screens/screen_<time>.png")
    p.add_argument("--crop", type=int, nargs=4, metavar=("X", "Y", "W", "H"),
                   help="only this part of the game view (client pixels), e.g. a gump")
    p.set_defaults(fn=cmd_screenshot)
    _know_parser(sub)
    return ap


def _know_parser(sub):
    import knowledge
    p = sub.add_parser("know", help="long-term memory: facts, procedures, episodes, preferences, insights")
    ks = p.add_subparsers(dest="know_op", required=True)
    for name in ("add", "update"):
        q = ks.add_parser(name)
        if name == "update":
            q.add_argument("id", type=int)
            q.add_argument("--topic")
        else:
            q.add_argument("--kind", required=True, choices=knowledge.KINDS)
            q.add_argument("--topic", required=True)
            q.add_argument("--entity", action="append", help="NPC/player/item/place it's about (repeatable)")
            q.add_argument("--supersedes", type=int)
        q.add_argument("content", nargs="*" if name == "update" else "+")
        q.add_argument("--tags", help="comma-separated")
        q.add_argument("--at", type=int, nargs="+", metavar="N", help="X Y or FACET X Y")
        q.add_argument("--source", choices=tuple(knowledge.SOURCES), default="observed" if name == "add" else None)
        q.add_argument("--ref", help="evidence: capture tag, chat#id, juncture#id, URL, screenshot path")
        q.add_argument("--confidence", type=float)
        q.add_argument("--importance", type=int, default=5 if name == "add" else None)
    q = ks.add_parser("confirm")
    q.add_argument("id", type=int)
    q.add_argument("--source", choices=tuple(knowledge.SOURCES), default="observed")
    q.add_argument("--ref")
    q = ks.add_parser("retract")
    q.add_argument("id", type=int)
    q.add_argument("--reason", required=True)
    q = ks.add_parser("get")
    q.add_argument("id", type=int)
    q.add_argument("--history", action="store_true")
    q = ks.add_parser("search")
    q.add_argument("query", nargs="*")
    q.add_argument("--kind", choices=knowledge.KINDS)
    q.add_argument("--tag", action="append")
    q.add_argument("--near", type=int, nargs="+", metavar="N", help="X Y or FACET X Y")
    q.add_argument("--here", action="store_true", help="boost entries near where you stand")
    q.add_argument("--limit", type=int, default=10)
    q.add_argument("--all", action="store_true", help="include superseded/retracted")
    q = ks.add_parser("brief", help="what to remember now (position, NPCs, junctures, intent)")
    q.add_argument("--limit", type=int, default=12)
    q = ks.add_parser("review", help="unconfirmed inferences, stale entries, topics with several facts")
    q.add_argument("--stale-days", type=float, default=30.0)
    ks.add_parser("stats")
    p.set_defaults(fn=cmd_know)


def main(argv=None) -> int:
    try:
        a = build_parser().parse_args(argv)
    except CtlError as e:
        out = {"ok": False, "error": str(e)}
    else:
        mem = Memory(a.db)
        try:
            out = a.fn(a, mem)
        except CtlError as e:
            out = {"ok": False, "error": str(e)}
        finally:
            mem.close()
    print(json.dumps(out, default=str), flush=True)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

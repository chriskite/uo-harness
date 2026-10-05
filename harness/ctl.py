"""Overseer control CLI (docs/OVERSEER.md): the AI overseer's hands and ears.

Tasks stay programmatic (loop_lumber.py, errand_bank.py); an AI overseer
supervises them through this CLI. There is no daemon: the SQLite store
(harness/memory.py) is the bus. Runners and task_wrap.py post junctures, the
user posts chat, and `ctl wait` blocks until one of them needs the overseer.

Usage: ./ctl.cmd [--db P] [--state-port N] [--control-port N] <cmd> ...
       (= python harness/ctl.py ...; ctl.cmd picks the Python 3.13 install, override with UO_PY)
  status                      proxy snapshot + running tasks + open junctures
  run <task> [args...]        start a whitelisted task (lumber, bank) detached
  stop [task_id]              stop the running task (-> task_failed juncture)
  wait [--timeout S] [--include-info]
                              block until a juncture (>= attention) or user chat; also
                              posts a `break_due` juncture when the agent gate's break
                              is due (grace before it starts by itself)
  break                       start the agent gate's break now (e.g. once home)
  alert <why> [--serial S]    possible staff (GM): gm_suspected juncture + staff alarm for
                              the human; repeats (runner hold, `wait`) until acked
  ack <juncture_id>           close a juncture (acking gm_suspected stops the alarm)
  junctures [--open] [--after N] [--limit N]
  chat [--after N] [--limit N] [--role R]
  say <text> | think <text> | note-action <text>
                              chat rows (role overseer; message/thought/action)
  act <name> [args]           one stock action through the proxy control port:
                              walk <dir 0-7> [n] [--walk], say <allowlisted>,
                              dclick <serial>, single_click <serial>, open_door,
                              target_cancel, goto <x> <y> | goto <mobile serial>
                              [--range R], menu <serial>, menu_pick <serial> <index>,
                              gump <serial> <button>, track <mode> | track off
  journal [--n N]             recent server messages, gumps, menus, vendor lists
Every call prints exactly one JSON object on stdout; exit 0 iff "ok" is true.
Global options go before the command.

Safety: `act gump` refuses the captcha (the human answers it, or the runner's
solver in captcha mode "auto"; ANTICHEAT.md §8.8/§8.13),
refuses gumps without reply buttons (§8.13 decoys flag any bot reply),
refuses buttons the layout doesn't offer, and on a gump that mentions
renouncing Young status allows only closing it (button 0). No raw packets;
speech is allowlisted; nothing is sent while a task runs (one character, no
interleaving), except `say` (free text, rule-8 filtered) and `single_click`
while a harvest job holds for speech (a `speech_nearby` juncture, speech_guard.py).
Opening a vendor's Buy list sends nothing further: the stock
client sends no packet when a shop window is closed without buying
(ClassicUO ShopGump.cs:590-610, Send_BuyRequest only on Accept).
"""
import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import actions  # noqa: E402
import alerts  # noqa: E402
from combat import (ATTACK_MIN_HP, CORPSE_GRAPHIC, DROP_AUTO, GOLD_GRAPHIC, LOOT_RANGE,  # noqa: E402
                    MAGERY_SPELLS, NOTORIETY, VIEW_RANGE)
import combat  # noqa: E402
import healing  # noqa: E402
import nav  # noqa: E402
import task_wrap as tw  # noqa: E402
import room  # noqa: E402
from room import ROOM_GUMP_ID, ROOM_REFUSED  # noqa: E402  (rental room menu; `act gump` refuses those buttons)
from humanize import PROFILES, Human  # noqa: E402
from memory import DEFAULT_DB, Memory  # noqa: E402
from uo import cliloc as cliloc_mod  # noqa: E402
from uo.gumps import controls as gump_controls, parse_layout  # noqa: E402
import tracking  # noqa: E402

HOST = "127.0.0.1"
TASKS = {"lumber": os.path.join(HERE, "loop_lumber.py"),
         "bank": os.path.join(HERE, "errand_bank.py"),
         "hunt": os.path.join(HERE, "loop_hunt.py")}
# Tests only: JSON {name: script path} replacing TASKS. Production never sets it.
TEST_TASKS_ENV = "UO_CTL_TEST_TASKS"
LOG_DIR = os.path.join(ROOT, "logs", "tasks")
# In-game speech allowlist (docs/PLAN.md "In-game speech is allowlisted keywords/
# commands only"; LUMBER_LOOP.md adds `room`). No code allowlist existed before
# this; these are the only phrases `act say` sends.
SPEECH_ALLOWLIST = ("bank", "room", "hello")
# While a harvest job holds for speech (speech_guard.py, a `speech_nearby` juncture with
# hold=true), the overseer may answer the speaker: free text, no longer than SAY_MAX, that
# never touches what AGENTS.md constraint 4 forbids (the harness, testing, automation, AI).
HOLD_ACTS = ("say", "single_click")
SAY_MAX = 120
REVEALING = re.compile(
    r"\b(bots?|botting|ai|a\.i|artificial|automat\w*|scripts?|scripted|scripting|macro\w*|program\w*"
    r"|harness|overseer|agents?|proxy|claude|anthropic|openai|gpt|chatgpt|llm|models?|test\w*"
    r"|experiment\w*|python|code|coding)\b", re.I)

HEARTBEAT_KEY = "overseer_heartbeat"
JUNCTURE_CURSOR_KEY = "overseer_juncture_cursor"
CHAT_CURSOR_KEY = "overseer_chat_cursor"
BREAK_NOTIFIED_KEY = "gate_break_due_notified"   # break_due_at of the last announced break
GATE_CHECK_S = 5.0                               # ctl wait: gate poll period
SEVERITY_RANK = {"info": 0, "attention": 1, "urgent": 2}
WAIT_MAX_EVENTS = 20
NEARBY_RANGE = 18
NEARBY_MAX = 30
ATTACKER_WINDOW_S = 10.0             # status.attackers: a swing at us this recent
WALK_MAX_STEPS = 20
LAYER_BACKPACK = 0x15
DIR_NAMES = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
# ClassicUO Game/Data/Layers.cs (0x1A-0x1C are vendor containers; 0x1D is the bank box)
LAYER_NAMES = {1: "one_handed", 2: "two_handed", 3: "shoes", 4: "pants", 5: "shirt", 6: "helmet",
               7: "gloves", 8: "ring", 9: "talisman", 0x0A: "necklace", 0x0B: "hair", 0x0C: "waist",
               0x0D: "torso", 0x0E: "bracelet", 0x0F: "face", 0x10: "beard", 0x11: "tunic",
               0x12: "earrings", 0x13: "arms", 0x14: "cloak", 0x15: "backpack", 0x16: "robe",
               0x17: "skirt", 0x18: "legs", 0x19: "mount", 0x1D: "bank"}
ACTS = ("walk", "say", "dclick", "single_click", "open_door", "target_cancel",
        "goto", "menu", "menu_pick", "gump", "unequip", "equip", "warmode", "attack", "loot",
        "target", "cast", "heal", "buy", "use", "drop", "track", "recall", "read_tomes", "room", "aspect")
# meta key: epoch seconds of ctl's last heal-potion drink (healing.PotionClock across ctl calls)
HEAL_POTION_KEY = "heal_potion_t"
PACK_ITEMS_MAX = 60                  # status.backpack.items
CONTAINER_ITEMS_MAX = 60             # status.containers[].items
DENY_TELEPORT_GRACE_S = 0.4           # after a walk deny, a teleporter may still move us (agent_link)
MOVE_GATE_WAIT_S = 10.0               # self-clearing proxy walk gates (agent_link.MOVE_GATE_WAIT_S)
WALK_OUTCOME_WAIT_S = 5.0             # confirm/deny, else the proxy's 3 s rejection (agent_link)
LOOT_MAX_ITEMS = 25
LAYER_BANK = 0x1D
CAST_CURSOR_WAIT_S = 4.0             # a spell's target cursor comes after its cast delay
BUY_CLILOC = 3006103                 # context menu "Buy"
VENDOR_RANGE = 12                    # 13 tiles got "too far away" live (docs/LUMBER_LOOP.md §13)
SORTED_BUY_CONTAINER = 0x2AF8        # ClassicUO BuyList: this container sorts by x; others map reversed
# A vendor takes what the pack lacks from the bank account and says so (live 2026-10-04, session
# 20261004_113229: Hackworth, 0 gp in the pack, bought 3 Trapped Pouches from Errol for 75 gp)
BANK_PAID = re.compile(r"The total of thy purchase is (\d+) gold, which has been withdrawn from your bank account")
POLICY_PATH = os.path.join(HERE, "data", "policy.json")
CAPTCHA_GUMP_ID = 0x00000001          # lumber.json captcha.gump_id; never answered by the overseer
GUMP_TEXT_MAX = 239                  # chars per gump text entry (the client's text box limit)
RENOUNCE_WORDS = ("renounce",)        # Young renounce prompt (clilocs 502085/3006307): close only
ROOM_VIA_KEY = "room_entered_via"    # meta: the keeper kind `room enter` used, for `room leave`'s default exit
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


def _age(now: float, t) -> float | None:
    return None if t is None else round(max(now - t, 0.0), 1)


def attackers(world: dict, pos, self_serial, now: float) -> list:
    """Mobiles the client has whose latest swing (S2C 0x2F) was at us within
    ATTACKER_WINDOW_S, nearest first."""
    me = None if self_serial is None else f"0x{_serial(self_serial):08X}"
    labels = world.get("labels") or {}
    out = []
    for key, sw in (world.get("swings") or {}).items():
        m = (world.get("mobiles") or {}).get(key)
        if m is None or sw.get("defender") != me or sw.get("t") is None or now - sw["t"] > ATTACKER_WINDOW_S:
            continue
        dist = nav.chebyshev((m["x"], m["y"]), (pos[0], pos[1])) if pos and m.get("x") is not None else None
        out.append({"serial": key, "name": m.get("name"), "label": labels.get(key), "dist": dist,
                    "hits": None if m.get("hits") is None else [m["hits"], m.get("hits_max")],
                    "last_swing_age_s": _age(now, sw["t"])})
    out.sort(key=lambda r: (r["dist"] is None, r["dist"] or 0))
    return out


def summarize(resp: dict, now: float | None = None) -> dict:
    """`status` from a state-port response. `mobiles` are the ones the client
    has (the world model prunes like the client: docs/WORLDMODEL.md "Pruning"),
    each with `age_s` since the server last updated it."""
    now = time.time() if now is None else now
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
                        "x": m["x"], "y": m["y"], "z": m.get("z"), "dist": dist,
                        "age_s": _age(now, m.get("seen_t"))})
    mobiles.sort(key=lambda m: (m["dist"] is None, m["dist"] or 0))
    items = world.get("items") or {}
    pack = combat.backpack(items, self_serial)
    counts, pack_items = {}, []
    if pack is not None:
        for k, it in combat.pack_items(items, pack):
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
                ground.append({"serial": key, "name": it.get("name") or _tile_name(it.get("graphic"), it.get("amount")),
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
        # Hunting mode, the arrow up now and recent hits (world model; a hit may be beyond the view)
        "tracking": {**(world.get("tracking") or {}), "hits": ((world.get("tracking") or {}).get("hits") or [])[-5:]},
        "movement": {k: mv.get(k) for k in ("inflight", "stalled", "resync_pending", "client_stale")},
        "gate": resp.get("gate"),
        "intent": resp.get("intent"), "intents": (resp.get("intents") or [])[-5:],
        "mobiles": mobiles[:NEARBY_MAX],
        "attackers": attackers(world, pos, self_serial, now),
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
                for k, it in combat.pack_items(items, root)]
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
    """Your buffs/debuffs (Outlands 0xFF sub 8, e.g. "Stationary Penalty").
    `description` is the server's text with its {value} placeholder; `values` are the
    buff's numbers, one per timer (for the Stationary Penalty {value} = `values[0]`, the
    steps left: docs/HUNT_LOOP.md), never durations. `ends_in_s`: seconds until it runs
    out on the server's clock (the world model's `ends_t`), None when it has no end;
    `expired` once that is past and the server hasn't removed it (it doesn't always: the
    stock client then keeps the icon too, but the effect is over). `raw` keeps the numeric
    fields whose meaning isn't decoded yet (f2 is always 1)."""
    now = time.time()
    out = []
    for icon, b in ((world.get("buffs") or {}).get(me.get("serial") or "", {}) or {}).items():
        title = b.get("title") or (cliloc_text(b["cliloc"]) if b.get("cliloc") else "")
        ends_in = None if b.get("ends_t") is None else round(b["ends_t"] - now, 1)
        out.append({"icon": int(icon), "title": title, "description": b.get("description") or None,
                    "values": [t.get("value") for t in b.get("timers") or []],
                    "ends_in_s": ends_in, "expired": ends_in is not None and ends_in <= 0,
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


def item_label(it: dict, amount: int | None = None) -> str | None:
    """What an item is called: its clicked name, else the tiledata name. `amount` is how
    many the sentence is about (a plural ending for more than one); default the stack."""
    name = it.get("name") or _tile_name(it.get("graphic"), it.get("amount") if amount is None else amount) or ""
    return item_name(name) or None


def _tile_name(graphic, amount=None):
    """Tiledata name of an item graphic (install dir, read-only), or None; the plural
    ending shown for an `amount` above 1, the singular otherwise (uomap.display_name)."""
    if graphic is None:
        return None
    try:
        import uomap
        it = uomap.tiledata().item(graphic)
    except (OSError, ValueError):
        return None
    return uomap.display_name(it.name, (amount or 1) > 1) if it else None


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


def gate_query(port: int, action: str | None = None, timeout: float = 3.0) -> dict:
    """The proxy's agent gate ({"op": "gate"}[, action]): its JSON response."""
    req = {"op": "gate", **({"action": action} if action else {})}
    with socket.create_connection((HOST, port), timeout=timeout) as s:
        s.sendall((json.dumps(req) + "\n").encode())
        return json.loads(s.makefile("rb").readline() or b"{}")


def check_break_due(mem, port: int):
    """Post one `break_due` juncture (attention) for each due break of the agent
    gate, so a waiting overseer wakes while the agent can still act: it may stop
    the task, head somewhere safe and `ctl break`. Otherwise the break starts by
    itself when the grace runs out, wherever the character stands."""
    try:
        gate = gate_query(port).get("gate") or {}
    except (OSError, ValueError):
        return                                       # proxy down: nothing to announce
    due = gate.get("break_due_at")
    if gate.get("state") != "break_due" or due is None \
            or tw.meta_get(mem, BREAK_NOTIFIED_KEY, "") == f"{due:.3f}":
        return
    starts = gate.get("break_starts_in_s") or 0.0
    at = time.strftime("%H:%M", time.localtime(time.time() + starts))
    mem.juncture("gate", "break_due",
                 f"Scheduled break is due: it starts by itself at {at} (in {starts / 60:.0f} min) wherever "
                 f"the character is. Stop or finish the task, get somewhere safe, then `ctl break`.",
                 severity="attention",
                 data={"break_due_at": due, "break_starts_in_s": starts, "starts_at": at})
    tw.meta_set(mem, BREAK_NOTIFIED_KEY, f"{due:.3f}")


def cmd_break(a, mem):
    heartbeat(mem)
    try:
        resp = gate_query(a.state_port, "break")
    except OSError as e:
        raise CtlError(f"proxy state port {a.state_port} unreachable: {e}")
    gate = resp.get("gate") or {}
    if not resp.get("ok"):
        raise CtlError(f"break refused: {resp.get('error')}")
    until = time.strftime("%H:%M", time.localtime(gate["break_until"])) if gate.get("break_until") else "?"
    mem.chat_post("overseer", f"break: started, until {until}", "action", data={"cmd": "break", "gate": gate})
    return {"ok": True, "gate": gate, "break_until": until}


def cmd_wait(a, mem):
    jcur = int(tw.meta_get(mem, JUNCTURE_CURSOR_KEY, "0"))
    ccur = int(tw.meta_get(mem, CHAT_CURSOR_KEY, "0"))
    min_rank = 0 if a.include_info else SEVERITY_RANK["attention"]
    end = None if a.timeout <= 0 else time.monotonic() + a.timeout
    next_gate = 0.0
    while True:
        heartbeat(mem)
        if time.monotonic() >= next_gate:
            check_break_due(mem, a.state_port)
            next_gate = time.monotonic() + GATE_CHECK_S
        alerts.staff_alarm_due(mem)                  # repeats while a gm_suspected juncture is open
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


def cmd_alert(a, mem):
    """The overseer suspects staff (a GM): post `gm_suspected` (urgent) and sound
    the staff alarm for the human at the PC. It repeats every alerts.STAFF_REPEAT_S
    from a holding runner and from `ctl wait` until the juncture is acked."""
    heartbeat(mem)
    reason = " ".join(a.reason).strip()
    if not reason:
        raise CtlError("alert <why you suspect staff> [--serial S]")
    data = {"reason": reason, "serial": None if a.serial is None else f"0x{_parse_serial(a.serial):08X}"}
    if alerts.open_gm(mem):
        jid = alerts.open_gm(mem)[0]
        alerts.staff(True)
        out = {"ok": True, "id": jid, "already_open": True}
    else:
        jid = alerts.post_gm(mem, "ctl", reason, data)
        out = {"ok": True, "id": jid}
    mem.chat_post("overseer", f"alert: possible staff: {reason}", "action", data={"cmd": "alert", "id": jid, **data})
    return out


def cmd_ack(a, mem):
    heartbeat(mem)
    ok = mem.juncture_ack(a.juncture_id)
    return {"ok": ok, "id": a.juncture_id, **({} if ok else {"error": "no such open juncture"})}


def cmd_junctures(a, mem):
    # Without --after: the newest `limit` (live 2026-10-03 the oldest 100 hid an open speech hold).
    return {"ok": True, "junctures": mem.junctures(after_id=a.after or 0, open_only=a.open, limit=a.limit,
                                                   newest=a.after is None)}


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
    pkt = actions.walk(d, run=not a.walk)
    walkers = None
    if not a.no_map:
        import pathfind
        walkers = pathfind.Walkers()
    sent_at = 0.0
    for _ in range(n + 2):          # a turn costs one extra send
        before = pos
        st = stc.state()
        if walkers is not None and len(pos) > 3 and pos[3] == d:
            # the stock client checks each step against what it has now and sends nothing if it
            # can't be walked (PlayerMobile.Walk -> CanWalk); objects that just arrived count
            walk = walkers.get(st["world"]["self"].get("map"),
                               pathfind.ground_items(st["world"]["items"].values()))
            if walk is not None and walk.can_walk(pos[0], pos[1], pos[2], d) is None:
                outcomes.append("blocked")
                stop = "blocked (the map says the step can't be walked; the client wouldn't send it)"
                break
        human.pace_step(not a.walk, sent_at, bool(st["movement"].get("mounted")))   # the proxy's floor too
        deadline = time.monotonic() + MOVE_GATE_WAIT_S
        while True:                  # proxy walk gates that clear on their own, like Mover.step
            resp = ctl.send(pkt)
            if not resp.startswith("ERR walk gated:") or "stalled" in resp or time.monotonic() > deadline:
                break
            time.sleep(0.02 if "pacing" in resp else 0.1)
        if resp != "OK":
            stop = resp
            break
        sent_at = time.monotonic()
        end = time.monotonic() + WALK_OUTCOME_WAIT_S
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


def speech_hold(mem: Memory) -> dict | None:
    """The open `speech_nearby` juncture of a task holding for the overseer, if any."""
    row = mem.con.execute("SELECT id FROM junctures WHERE kind='speech_nearby' AND acked_t IS NULL "
                          "ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    j = mem.junctures(after_id=row[0] - 1, limit=1)[0]
    return j if j["data"].get("hold") else None


def conversation_text(raw: str) -> str:
    """Free text for answering someone while a job holds for speech, or CtlError."""
    if not raw or len(raw) > SAY_MAX or not raw.isprintable():
        raise CtlError(f"say: 1..{SAY_MAX} printable characters")
    m = REVEALING.search(raw)
    if m:
        raise CtlError(f"say refused: {m.group(0)!r} could reveal the harness (AGENTS.md constraint 4); rephrase")
    return raw


def _act(a, mem) -> dict:
    alive = running_tasks(mem)
    hold = speech_hold(mem) if alive else None
    if alive and not (hold and a.name in HOLD_ACTS):
        raise CtlError(f"task {alive[0]['task_id']} is running; no interleaved actions (stop it first)"
                       + ("; while it holds for speech only `say` and `single_click`" if hold else ""))
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
    if a.name == "heal":
        return _act_heal(a, mem)
    if a.name == "track":
        return _act_track(a)
    if a.name == "buy":
        return _act_buy(a, mem)
    if a.name == "use":
        return _act_use(a)
    if a.name == "drop":
        return _act_drop(a)
    if a.name == "recall":
        return _act_recall(a, mem)
    if a.name == "read_tomes":
        return _act_read_tomes(a)
    if a.name == "room":
        return _act_room(a, mem)
    if a.name == "aspect":
        return _act_aspect(a)
    pkt = None
    serial = None
    if a.name == "say":
        raw = " ".join(a.args).strip()
        if raw.lower() in SPEECH_ALLOWLIST:
            pkt = actions.say_unicode(raw.lower())
        elif hold is None:
            raise CtlError(f"speech not allowlisted: {raw!r}; allowed: {list(SPEECH_ALLOWLIST)} (free text only "
                           f"to answer someone while a harvest job holds for speech)")
        else:
            pkt = actions.say_unicode(conversation_text(raw))
    elif a.name in ("dclick", "single_click", "menu"):
        if len(a.args) != 1:
            raise CtlError(f"{a.name} <serial>")
        serial = _parse_serial(a.args[0])
    elif a.name == "menu_pick":
        if len(a.args) != 2:
            raise CtlError("menu_pick <serial> <entry index>")
        try:
            idx = int(a.args[1], 0)
        except ValueError:
            raise CtlError("menu_pick: entry index must be an integer")
        serial = _parse_serial(a.args[0])
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
        pkts, opened = [pkt], []
        if serial is not None:
            pkts = _click_packets(a.name, serial, stc, idx if a.name == "menu_pick" else None)
            if a.name != "menu_pick":  # a picked menu's item was already on screen
                opened = _open_first(ctl, stc, Human(a.human, seed=a.seed), (serial, False))
        if a.name == "target_cancel":
            cur = (stc.state().get("world") or {}).get("target") or {}
            if not cur.get("active") or cur.get("cursor_id") is None:
                raise CtlError("no target cursor is up")
            pkts = [actions.target_cancel(cur["cursor_id"], cur.get("target_type") or 0,
                                          cur.get("cursor_type") or 0)]
        if a.name == "gump":
            pkts = [gump_reply(stc.state(), a.args[0], a.args[1], a.text or ())]
        mark = stc.mark()
        for p in pkts:                 # companion packets go out back to back, as the client sends them
            resp = ctl.send(p)
            if resp != "OK":
                return {"ok": False, "reply": resp}
        if a.name == "menu":           # wait for the server's context menu
            got = stc.wait_events(mark, lambda evs: any(e.get("ev") == "popup" for e in evs))
        else:                          # what the server answered, as the player would read it
            got = stc.wait_events(mark, lambda evs: False, timeout=1.5)
        heard = [journal_view(e) for e in got if e.get("ev") in JOURNAL_EVS]
        out = {"ok": True, "reply": resp, "heard": heard}
        if opened:
            out["opened"] = opened
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


def _open_first(ctl: "Control", stc: "StateConn", human, *needs) -> list[str]:
    """Open the containers the stock client would need on screen first
    (agent_link.containers_to_open; `needs` are (serial, itself) pairs, itself
    for a drop destination), outermost first, each with the stock double-click
    and a wait for the server's 0x24, then a moment to find the item. Returns
    the opened serials. CtlError when one can't be opened (the bank box only
    opens by saying `bank`; a ground container must be in view)."""
    from agent_link import OPEN_WAIT_S, closed_bank, containers_to_open
    st = stc.state()
    world = st["world"]
    todo = []
    for serial, itself in needs:
        todo += [s for s in containers_to_open(world, serial, itself) if s not in todo]
    if closed_bank(world, todo) is not None:
        raise CtlError("your bank box isn't open: say `bank` near a banker first")
    opened = []
    for s in todo:
        key = f"0x{s:08X}"
        why = not_clickable(world, st["movement"].get("pos"), s)
        if why is not None:
            raise CtlError(f"container {key} would have to be opened first: {why}")
        resp = ctl.send(actions.dclick(s))
        if resp != "OK":
            raise CtlError(f"opening container {key}: {resp}")
        end = time.monotonic() + OPEN_WAIT_S
        while key not in {f"0x{_serial(c):08X}" for c in stc.state()["world"].get("containers") or []}:
            if time.monotonic() > end:
                raise CtlError(f"container {key} didn't open")
            time.sleep(0.1)
        human.wait("find")
        opened.append(key)
    return opened


def not_clickable(world: dict, pos, serial: int) -> str | None:
    """Why the stock client couldn't click `serial` right now, else None.

    The client can only click what it has on screen: the entity must be in the
    world model, and it (or, for an item in a container, the mobile or ground
    item holding it) within the client's view range of the player. The client
    drops objects beyond its view range (World.ProcessDeletes, range from S2C
    0xC8) and the world model prunes the same way, so a serial known only from
    a last-seen position (`ctl npcs`, world `last_seen`) isn't clickable (live
    20260930_091704: a context-menu request to a vendor 33 tiles away)."""
    me = world["self"].get("serial")
    me = _serial(me) if me is not None else None
    if serial == me:
        return None
    key = f"0x{serial:08X}"
    ent = world["mobiles"].get(key) or world["items"].get(key)
    if ent is None:
        return f"{key} is not in the client's world (never seen, or gone)"
    seen = set()
    while ent.get("container") is not None:
        parent = _serial(ent["container"])
        if parent == me:
            return None
        pkey = f"0x{parent:08X}"
        ent = None if parent in seen else world["mobiles"].get(pkey) or world["items"].get(pkey)
        if ent is None:
            return f"{key} is inside {pkey}, which the client doesn't have"
        seen.add(parent)
    if ent.get("x") is None or ent.get("y") is None or not pos:
        return f"{key} has no known position"
    dist = nav.chebyshev(tuple(pos[:2]), (ent["x"], ent["y"]))
    view = min(VIEW_RANGE, world.get("view_range") or VIEW_RANGE)   # the client's 0xC8 range
    if dist > view:
        return f"{key} is {dist} tiles away, beyond the client's {view}-tile view (goto it first)"
    return None


def open_popup(events: list) -> dict | None:
    """The context menu the client shows, from world event envelopes: the latest
    server menu (0xBF 0x14) with no selection or new menu request after it
    (the client shows one context menu at a time)."""
    menu = None
    for e in events:
        d = e.get("data") or {}
        if e.get("origin") == "world" and d.get("ev") in ("popup", "popup_request", "popup_select"):
            menu = d if d["ev"] == "popup" else None
    return menu


def context_menu_packets(serial: int) -> list[bytes]:
    """A right-click as the stock client sends it: 0x09 then 0xBF 0x13, back to
    back (DelayedObjectClickManager.Update: SingleClick, then OpenPopupMenu, the
    click because Outlands has tooltips off; human captures 30/30, 0-1 ms apart)."""
    return [actions.single_click(serial), actions.request_popup(serial)]


def _click_packets(name: str, serial: int, stc: "StateConn", index: int | None) -> list[bytes]:
    """Packets for dclick / single_click / menu / menu_pick on `serial`, shaped like
    the stock client's, or CtlError when the client couldn't do it right now."""
    st = stc.state()
    world = st["world"]
    why = not_clickable(world, st["movement"].get("pos"), serial)
    if why is not None:
        raise CtlError(why)
    key = f"0x{serial:08X}"
    me = world["self"]
    mobile = key in world["mobiles"] and _serial(me.get("serial") or 0) != serial
    if name == "dclick":
        if mobile and me.get("warmode"):
            # the stock client never sends 0x06 for a mobile in war mode: it attacks (GameActions.DoubleClick)
            raise CtlError("in war mode a double-click on a mobile attacks; use `act attack` or `act warmode off`")
        return [actions.dclick(serial)]
    if name == "single_click":
        # a mobile's click comes with its status request (captures: 1834 of 1834 0x09 on a mobile, 0 ms apart)
        return [actions.single_click(serial)] + ([actions.status_request(serial)] if mobile else [])
    if name == "menu":
        return context_menu_packets(serial)
    menu = open_popup(stc.events(0)[0])
    if menu is None or _serial(menu.get("serial")) != serial:
        raise CtlError(f"no context menu of {key} is open (`act menu {key}` first)")
    if index not in [e.get("index") for e in menu.get("entries") or []]:
        raise CtlError(f"entry {index} is not in {key}'s context menu: "
                       f"{[e.get('index') for e in menu.get('entries') or []]}")
    return [actions.popup_selection(serial, index)]


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
    """warmode on|off: 0x72, the stock Tab toggle (so nothing is sent when war
    mode is already in that state: Tab only ever flips it). attack <serial>: a
    hostile monster only (never a player, a player's pet, an NPC or anything with
    a human body; threats.identify + notoriety 3-6), on screen. Like the stock
    client (Tab, then double-click the target: GameActions.DoubleClick) it turns
    war mode on first, then sends 0x34 unless the client has an outstanding status
    request for that mob (world.status_requested: ClassicUO RequestMobileStatus sends it
    while HitsRequest < Received, whatever hits it already knows) and 0x05. Refused below
    ATTACK_MIN_HP of max hits."""
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
            if bool(me.get("warmode")) == on:
                return {"ok": True, "reply": "already", "warmode": on}
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
        ok, why = combat.attackable(world, key)
        if not ok:
            raise CtlError(why)
        why = not_clickable(world, st["movement"].get("pos"), serial)
        if why is not None:
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
        resp = "OK"
        for pkt in combat.attack_packets(world, serial):
            resp = ctl.send(pkt)
            if resp != "OK":
                break
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
    at a time (a human pause, then 0x07 lift and 0x08 drop back to back, the
    stock GrabItem shape, GameActions.cs:819-852), gold first, at most
    --max-items, stopping at your weight limit. Refuses corpses with a human
    body (players, human NPCs, your own: policy, no corpse runs)."""
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
        if combat.human_corpse(corpse):
            raise CtlError(f"{key} ({name or f'body 0x{body:X}'}) is a human corpse (a player, a human NPC or "
                           "you): not looted (criminal, and policy: no corpse runs)")
        dist = nav.chebyshev(tuple(pos[:2]), (corpse["x"], corpse["y"]))
        if dist > LOOT_RANGE:
            raise CtlError(f"the corpse is {dist} tiles away; walk within {LOOT_RANGE} first "
                           f"(goto {key} --range 1)")
        pack = combat.backpack(world["items"], me_serial)
        if pack is None:
            raise CtlError("backpack not known to the world model")
        noto_before = world["self"].get("notoriety")
        mark = stc.mark()
        human = Human(a.human, seed=a.seed)
        opened = _open_first(ctl, stc, human, (pack, True))     # the loot is dragged into the open backpack
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
        order = combat.loot_order(inside)
        taken, failed, stopped = [], [], None
        for k, it in order[:a.max_items]:
            me = stc.state()["world"]["self"]
            if me.get("weight") is not None and me.get("weight_max") and me["weight"] >= me["weight_max"]:
                stopped = f"weight {me['weight']}/{me['weight_max']}"
                break
            human.wait("drag")
            s = _serial(k)
            if any(ctl.send(p) != "OK" for p in combat.grab_packets(s, it.get("amount") or 1, pack)):
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
                   "name": it.get("name") or _tile_name(it.get("graphic"), it.get("amount")), "amount": it.get("amount")}
            (taken if moved else failed).append(row if moved else k)
        got = stc.wait_events(mark, lambda evs: False, timeout=0.5)
        noto_after = stc.state()["world"]["self"].get("notoriety")
        out = {"ok": not failed, "corpse": {"serial": key, "name": name or None,
                                            "body": None if body is None else f"0x{body:04X}", "dist": dist},
               "taken": taken, "failed": failed, "left": max(0, len(inside) - len(taken)),
               "heard": [journal_view(e) for e in got if e.get("ev") in JOURNAL_EVS]}
        if opened:
            out["opened"] = opened
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
                pack = combat.backpack(world["items"], me_serial)
                c, depth = it.get("container"), 0
                while c is not None and _serial(c) != pack and depth < 8:
                    c, depth = (world["items"].get(f"0x{_serial(c):08X}") or {}).get("container"), depth + 1
                if pack is None or c is None or _serial(c) != pack:
                    raise CtlError(f"{key} isn't in your backpack")
                from agent_link import containers_to_open
                closed = containers_to_open(world, serial)
                if closed:
                    # with the cursor up a click targets: the player opens the bag before using the tool
                    raise CtlError(f"{key} is in a container the client hasn't opened "
                                   f"({', '.join(f'0x{s:08X}' for s in closed)}): `act target_cancel`, open "
                                   f"it (`act dclick`), then use the tool again")
                # the client sends a contained item's container-local x/y and z (actions.target_object)
                x, y, z = it.get("x") or 0, it.get("y") or 0, it.get("z") or 0
                graphic, what = it.get("graphic") or 0, it.get("name") or _tile_name(it.get("graphic"), it.get("amount"))
            else:
                ok, why = combat.attackable(world, key)
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
    sid = combat.spell_id(text)
    if sid is None:
        raise CtlError(f"unknown spell {text!r}; Magery spells: {', '.join(MAGERY_SPELLS)}")
    return sid


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


def _act_heal(a, mem) -> dict:
    """heal [--gheal-min-missing N]: one heal on yourself, chosen like the hunt runner
    (healing.py). A heal potion when one can be drunk (its bags opened first, the stock
    double-click), else Heal or Greater Heal by the missing hits (cast, then the
    cursor answered with yourself). A potion the server refuses as still cooling down
    (cliloc 500235) falls back to the spell in the same call."""
    if a.args:
        raise CtlError("heal takes no arguments (options: --gheal-min-missing N)")
    last = tw.meta_get(mem, HEAL_POTION_KEY)
    clock = healing.PotionClock(None if last is None else float(last))
    ctl, stc = _connect(a)
    try:
        st = stc.state()
        me = st["movement"].get("self_serial")
        choice = healing.choose(st["world"], me, clock.ready(time.time()), a.gheal_min_missing)
        out, heard = {"missing": choice.missing}, []
        if choice.kind == "potion":
            serial = _serial(choice.potion)
            opened = _open_first(ctl, stc, Human(a.human, seed=a.seed), (serial, False))
            if opened:
                out["opened"] = opened
            mark = stc.mark()
            stc.intent("Drinking a heal potion", "heal")
            resp = ctl.send(actions.dclick(serial))
            if resp != "OK":
                return {"ok": False, "reply": resp, **out}
            answers = (healing.CLILOC_HEALED, healing.CLILOC_POTION_WAIT, healing.CLILOC_FULL_HEALTH)
            got = stc.wait_events(mark, lambda evs: any(e.get("ev") == "cliloc" and e.get("cliloc") in answers
                                                        for e in evs), timeout=2.0)
            tw.meta_set(mem, HEAL_POTION_KEY, f"{time.time():.2f}")
            heard += [journal_view(e) for e in got if e.get("ev") in JOURNAL_EVS]
            if healing.CLILOC_POTION_WAIT not in {e.get("cliloc") for e in got if e.get("ev") == "cliloc"}:
                return {"ok": True, "reply": resp, "used": "potion", "potion": choice.potion, "why": choice.why,
                        "hits": _self_hits(stc), **out, "heard": heard}
            out["potion_refused"] = choice.potion
            st = stc.state()
            choice = healing.choose(st["world"], me, False, a.gheal_min_missing)
        if choice.kind != "spell":
            if choice.missing <= 0 and not heard:
                return {"ok": True, "reply": choice.why, **out}
            raise CtlError(f"no heal possible: {choice.why}")
        name = MAGERY_SPELLS[choice.spell - 1]
        mark = stc.mark()
        stc.intent(f"Casting {name} on myself", "heal")
        resp = ctl.send(actions.cast_spell(choice.spell))
        if resp != "OK":
            return {"ok": False, "reply": resp, "spell": name, **out}
        got = stc.wait_events(mark, lambda evs: any(e.get("ev") in ("target", "cliloc") for e in evs),
                              timeout=CAST_CURSOR_WAIT_S)
        heard += [journal_view(e) for e in got if e.get("ev") in JOURNAL_EVS]
        cur = stc.state()["world"].get("target") or {}
        if not cur.get("active") or cur.get("cursor_id") is None:
            return {"ok": False, "reply": "no target cursor", "spell": name, **out, "heard": heard}
        Human(a.human, seed=a.seed).wait("aim")
        st = stc.state()
        now = st["world"].get("target") or {}
        if not now.get("active") or now.get("cursor_id") != cur["cursor_id"]:
            return {"ok": False, "reply": "the spell's cursor went away", "spell": name, **out, "heard": heard}
        mark = stc.mark()
        resp = ctl.send(combat.target_self(cur, me, st["movement"]["pos"], st["world"]["self"].get("body")))
        got = stc.wait_events(mark, lambda evs: any(e.get("ev") == "cliloc" and e.get("cliloc") == healing.CLILOC_HEALED
                                                    for e in evs), timeout=2.0)
        heard += [journal_view(e) for e in got if e.get("ev") in JOURNAL_EVS]
        return {"ok": resp == "OK", "reply": resp, "used": "spell", "spell": name, "why": choice.why,
                "hits": _self_hits(stc), **out, "heard": heard}
    finally:
        ctl.close()
        stc.close()


def _self_hits(stc) -> list:
    me = stc.state()["world"]["self"]
    return [me.get("hits"), me.get("hits_max")]


def track_mode(words) -> str:
    """A Hunting mode from words (tracking.parse_mode)."""
    try:
        return tracking.parse_mode(words)
    except ValueError as e:
        raise CtlError(str(e))


class _CtlIO:
    """escape.recall's and tracking.hunt's IO over ctl's control and state connections;
    a proxy refusal raises `error`."""

    def __init__(self, ctl, stc, error):
        self.ctl, self.stc, self.error = ctl, stc, error
        self.cursor = stc.mark()

    def send(self, pkt: bytes):
        resp = self.ctl.send(pkt)
        if resp != "OK":
            raise self.error(f"proxy refused: {resp}")

    def poll(self):
        evs, self.cursor = self.stc.events(self.cursor)
        return self.stc.state(), [e["data"] for e in evs if e.get("origin") == "world"]


def _act_recall(a, mem) -> dict:
    """recall [book serial] [--rune NAME] [--check] | recall --witcher N [--library L] |
    recall --library L --rune NAME: recall to the default rune of a runebook or rune
    tome in your backpack (harness/escape.py, the job runners' red escape), to a named
    rune of your runebook or rune tome (--rune; without a serial the backpack book
    whose remembered runes hold that name, else the first book), or with a rune
    library's tome (harness/places.py, harness/data/rune_libraries.json; stand within
    2 tiles of the tome): Witcher rune N (--witcher; the library you stand in, else
    --library, else Cambria) or a library row by name (--library L --rune NAME: the
    exact name or words only it holds; `ctl runes find` lists them). A charge when the
    book has one, else the Recall spell. --check only opens your book and reads it
    (escape.read_book: default rune, charges, every rune's name and tile; a tome page
    by page) and remembers it for this character (places.remember_book, meta
    `own_books`: `runes near` and the lumber loop's landings use it); with --library
    it only reads the library tome's default and charges. Without a serial: the first
    book found, tomes first."""
    import escape
    import places
    ctl, stc = _connect(a)
    try:
        st = stc.state()
        me = st["movement"].get("self_serial")
        character = (st["world"].get("self") or {}).get("name")
        books = escape.find_books(st["world"], me)
        rune, want, own = a.rune, None, None
        pos = st["movement"].get("pos")
        if a.witcher or a.library:
            lib_id = a.library
            if lib_id is None:
                here = places.library_at(pos, (st["world"].get("self") or {}).get("map"))
                lib_id = here["id"] if here else "cambria"
            query = a.witcher or a.rune
            if not query:
                raise CtlError("recall --library L needs --rune NAME or --witcher N")
            try:
                lib = places.library(lib_id)
                want = places.library_rune(lib_id, query, a.tome)
                if a.witcher and want["witcher"] is None:
                    raise KeyError(f"no Witcher rune {a.witcher!r} in the {lib['name']}")
            except KeyError as e:
                raise CtlError(e.args[0])
            book = places.tome_at_hand(st["world"], pos, want["tome"], lib_id)
            if book is None:
                raise CtlError(f"{want['name']!r} is in tome {want['tome']} ({want['tome_title']}) at the "
                               f"{lib['name']}, {tuple(want['tome_pos'][:2])}: stand within {lib['use_range']} "
                               f"tiles of it ({lib['stand']}) first")
            rune = want["name"]
        elif a.args:
            book = _parse_serial(a.args[0])
            if book not in [b for b, _ in books]:
                raise CtlError(f"0x{book:08X} isn't a runebook or rune tome in your backpack")
        elif books:
            book = books[0][0]
            if a.rune:              # the backpack book whose remembered runes hold that name
                held = {b for b, _ in books}
                own = next(((int(kb["serial"], 16), r) for kb in places.known_books(mem, character)
                            if int(kb["serial"], 16) in held for r in kb.get("runes") or []
                            if escape.rune_matches(r["name"], a.rune)), None)
                if own:
                    book = own[0]
        else:
            raise CtlError("no runebook or rune tome in your backpack")
        if a.rune and want is None and own is None:
            own = next(((book, r) for kb in places.known_books(mem, character) if int(kb["serial"], 16) == book
                        for r in kb.get("runes") or [] if escape.rune_matches(r["name"], a.rune)), None)
        io = _CtlIO(ctl, stc, escape.RecallError)
        weapon = next((k for k, it in st["world"]["items"].items()
                       if it.get("layer") in (1, 2) and it.get("container") is not None
                       and _serial(it["container"]) == me), None)
        try:
            if a.check and want is None:
                human = Human(a.human, seed=a.seed)
                info = escape.read_book(io, book, wait=lambda: human.wait("read"))
                if character:
                    places.remember_book(mem, character, info)
                return {"ok": True, "book": info["serial"], **info, "character": character,
                        "remembered": bool(character),
                        "can_cast": escape.can_cast_recall(st["world"], me, (st["world"].get("self") or {}).get("mana"))}
            if a.check:
                return {"ok": True, "book": f"0x{book:08X}", **escape.check_ready(io, book)}
            where = rune if want is None else (f"Witcher rune {want['witcher']} "
                                               f"({places.witcher_rune(want['witcher'])['name']})"
                                               if want["witcher"] else want["name"])
            stc.intent(f"Recalling to {where}" if where else "Recalling home", "travel")
            out = escape.escape(io, book, attempts=1, log=lambda m: None, rune=rune)
        except escape.RecallError as e:
            raise CtlError(str(e))
        res = {**out, "book": f"0x{book:08X}"}
        if want and out.get("ok") and out.get("to"):
            res["library"] = lib_id
        exp = want or (own[1] if own else None)
        if exp and out.get("ok") and out.get("to"):
            res["expected"] = [exp["x"], exp["y"]]
            res["on_rune"] = exp["x"] is not None and max(abs(out["to"][0] - exp["x"]),
                                                            abs(out["to"][1] - exp["y"])) <= 2
        if weapon is not None:
            res["weapon"] = _rewield(ctl, stc, me, weapon, Human(a.human, seed=a.seed))
        return res
    finally:
        ctl.close()
        stc.close()


def _act_read_tomes(a) -> dict:
    """read_tomes <library id> [name words of a new library…]: learn a rune library
    (harness/places.py, harness/data/rune_libraries.json) by reading, without
    recalling, every rune tome lying within the library's use range (2) of where
    you stand: per tome its title, charges and every row's name and the tile it
    lands on (escape.read_runetome: the main page, then the detail pages pair by
    pair at a reading pace, then closed). Saves the library with your position as
    its stand: a known library's tomes are replaced (a tome that failed to read
    keeps its old entry), a new one needs a name. Commit the file afterwards."""
    import escape
    import places
    if not a.args:
        raise CtlError("read_tomes <library id> [name of a new library…]")
    lib_id = a.args[0].strip().lower()
    try:
        old = places.library(lib_id)
    except KeyError:
        old = None
    if old is None and len(a.args) < 2:
        raise CtlError(f"no rune library {lib_id!r} yet: name it, e.g. read_tomes {lib_id} <name words…>")
    ctl, stc = _connect(a)
    human = Human(a.human, seed=a.seed)
    try:
        st = stc.state()
        pos = st["movement"].get("pos")
        facet = int((st["world"].get("self") or {}).get("map") or 0)
        use_range = old["use_range"] if old else 2
        found = sorted(((int(k, 16), it) for k, it in st["world"]["items"].items()
                        if it.get("container") is None and it.get("x") is not None and pos
                        and max(abs(it["x"] - pos[0]), abs(it["y"] - pos[1])) <= use_range
                        and escape.book_kind(st["world"], int(k, 16)) == "runetome"),
                       key=lambda kv: (kv[1]["x"], kv[1]["y"], -(kv[1].get("z") or 0)))
        if not found:
            raise CtlError(f"no rune tome within {use_range} tiles of {pos[:2] if pos else pos}")
        io = _CtlIO(ctl, stc, escape.RecallError)
        before = {t["serial"]: t for t in (old or {}).get("tomes", [])}
        tomes, errors = [], {}
        today = time.strftime("%Y-%m-%d")
        for n, (serial, it) in enumerate(found, 1):
            key = f"0x{serial:08X}"
            stc.intent(f"Reading the rune tomes ({n}/{len(found)})", "read", target=(it["x"], it["y"]))
            r = None
            for _ in range(2):
                try:
                    r = escape.read_runetome(io, serial, wait=lambda: human.wait("read"))
                    break
                except escape.RecallError as e:
                    errors[key] = str(e)
                    human.wait("use")
            if r is None:
                if key in before:
                    tomes.append(before[key])
                continue
            errors.pop(key, None)
            tomes.append({"serial": key, "title": r["title"], "pos": [it["x"], it["y"], it.get("z")],
                          "read": today, "charges": r["charges"],
                          "rows": [{"name": x["name"], "x": x["x"], "y": x["y"]} for x in r["rows"]]})
            human.wait("use")
        lib = {**(old or {}), "id": lib_id, "name": " ".join(a.args[1:]) if len(a.args) > 1 else old["name"],
               "facet": facet, "stand": list(pos[:2]), "use_range": use_range, "tomes": tomes}
        places.save_library(lib)
        return {"ok": not errors, "library": lib_id, "name": lib["name"], "stand": lib["stand"],
                "tomes": [{"serial": t["serial"], "title": t["title"], "runes": len(t["rows"]),
                           "charges": t.get("charges")} for t in tomes],
                "runes": sum(len(t["rows"]) for t in tomes),
                "unread_tiles": [f"{t['title']}: {x['name']}" for t in tomes for x in t["rows"] if x["x"] is None],
                "gone": sorted(set(before) - {t["serial"] for t in tomes}), "errors": errors,
                "saved": os.path.relpath(places.LIBRARIES, ROOT),
                "reply": f"read {len(tomes)} tomes, {sum(len(t['rows']) for t in tomes)} runes"}
    finally:
        ctl.close()
        stc.close()


def _act_room(a, mem) -> dict:
    """room enter [OWNER WORDS…] | room leave [steward|town]: the rental room, as
    one deterministic act (harness/room.py, shared with the lumber runner;
    docs/NOTES.md "Rental room via the DTF house steward").

    enter: the nearest house steward or innkeeper in view (their click label) is
    walked to (2 tiles, guarded goto), right-clicked, "Room"/"Rent" picked; on the
    room menu (gump 0x8EAEFBDB) "Enter Your Room" when you rent one and no OWNER
    is named, else "Visit Other Rooms" and the row whose name holds OWNER's words
    (no OWNER: the only row). Done on "You enter the rental room." / facet 3.
    leave: the room's door (the nearest door item in view) is double-clicked and
    its menu's "Exit to House Steward" or "Exit to Town" pressed: as asked, else
    the way you came in (a steward or innkeeper), else the steward when offered.
    End Rental Contract / Expand are never pressed, with a reading pause before
    every press; a menu that doesn't offer the step is closed (button 0) and is an
    error that names what it offers."""
    import contextlib
    op = (a.args[0].lower() if a.args else "")
    if op not in ("enter", "leave"):
        raise CtlError("room enter [OWNER WORDS…] | room leave [steward|town]")
    human = Human(a.human, seed=a.seed)
    ctl, stc = _connect(a)
    io = _CtlIO(ctl, stc, room.RoomError)

    def heard(out: dict) -> dict:
        return {**out, "heard": [journal_view(e) for e in out["heard"]]}

    def walk(serial: int, rng: int):
        io.ctl.close()                # the walk opens its own control connection (agent_link.Link)
        try:
            with contextlib.redirect_stdout(sys.stderr):
                res = _act_goto(argparse.Namespace(**{**vars(a), "args": [f"0x{serial:08X}"], "range": rng,
                                                      "z": None, "max_moves": None}), mem)
        finally:
            io.ctl = Control(a.control_port)
        if not res["ok"]:
            raise CtlError(f"walking to {label}: {res.get('error') or res['reply']}")

    try:
        st = stc.state()
        facet = (st["world"].get("self") or {}).get("map")
        pos = st["movement"].get("pos")
        if op == "enter":
            if facet == room.ROOM_FACET:
                return {"ok": True, "already": "inside a rental room", "pos": pos, "facet": facet}
            found = room.find_keeper(st)
            label = found[1] if found else None
            if found:
                stc.intent(f"Entering a rental room via {label}", "room")
            out = heard(room.enter(io, human, a.args[1:], walk=walk))
            if out["ok"]:
                tw.meta_set(mem, ROOM_VIA_KEY, room.keeper_kind(out["via"]))
            stc.intent(f"In the rental room ({out['room']})" if out["ok"] else None, "room")
            return {**out, "reply": f"entered {out['room']}" if out["ok"] else out["error"]}
        # leave
        if facet != room.ROOM_FACET:
            raise CtlError(f"not in a rental room (facet {facet})")
        want = a.args[1].lower() if len(a.args) > 1 else None
        if want not in (None, "steward", "town"):
            raise CtlError("room leave [steward|town]")
        came = tw.meta_get(mem, ROOM_VIA_KEY)
        exit_to = want or (("town", "steward") if came == "innkeeper" else ("steward", "town"))
        stc.intent("Leaving the rental room", "room")
        out = heard(room.leave(io, human, exit_to))
        if out["ok"]:
            tw.meta_set(mem, ROOM_VIA_KEY, None)
        stc.intent(None)
        return {**out, "reply": f"left to {out['exit']}" if out["ok"] else out["error"]}
    except room.RoomError as e:
        raise CtlError(str(e))
    finally:
        io.ctl.close()
        stc.close()


def _act_aspect(a) -> dict:
    """aspect | aspect activate <weapon|spellbook|armor> [ASPECT]: the Aspect Mastery
    menu (harness/aspects.py, the same flow the lumber runner uses; docs/NOTES.md
    "Aspects"), opened by saying "[aspect" like a player. Bare: read it (Arcane
    Essence charges; per section the aspect, tier, xp, active tier) and close it.
    activate: step the section's arrows until it shows ASPECT (default: the one it
    shows), press Activate twice (the server asks "Click again to confirm."), close
    the menu. Costs 5 Arcane Essence ("already of that aspect" costs nothing);
    every armor piece must be worn for armor. Also reports the worn armor suit
    (aspects.suit: pieces, missing layers, pieces without the Harvest hue)."""
    import aspects
    args = [w.lower() for w in a.args]
    if args and (args[0] != "activate" or len(args) < 2 or args[1] not in aspects.SECTIONS):
        raise CtlError("aspect | aspect activate <weapon|spellbook|armor> [ASPECT]")
    section = args[1] if args else None
    want = " ".join(args[2:]) or None
    ctl, stc = _connect(a)
    try:
        io = _CtlIO(ctl, stc, aspects.AspectError)
        human = Human(a.human, seed=a.seed)
        try:
            if section is None:
                menu = aspects.read(io, human)
                arm = menu["sections"].get("armor") or {}
                out = {"ok": True, **menu, "reply": f"{menu['charges']} essence charges; armor "
                       f"{arm.get('aspect')} tier {arm.get('tier')}"}
            else:
                stc.intent(f"Activating the {want or section} aspect on the {section}", "aspect")
                res = aspects.activate(io, section, human, want)
                stc.intent(None)
                out = {**res, "section": section,
                       "reply": next((t for t in res["texts"] if aspects.activated_text(t) or aspects.already_text(t)),
                                     res.get("error"))}
        except aspects.AspectError as e:
            raise CtlError(str(e))
        st = stc.state()
        out["suit"] = aspects.suit(st["world"], st["movement"].get("self_serial"))
        return out
    finally:
        ctl.close()
        stc.close()


def _rewield(ctl, stc, me, key: str, human) -> dict:
    """A weapon worn before a cast that the cast put in the pack (an arcane staff below
    80 Arcane/Magery/Wrestling; live 2026-10-03 a tome-charge recall into Urukton left
    Shackleworth fighting with fists, then dead) is put back on with the stock drag
    (combat.equip_packets), like `act equip`. {serial, rewielded, error?}."""
    out = {"serial": key, "rewielded": False}
    end = time.monotonic() + 1.0             # the server moves it ~50 ms after the cast
    while True:
        st = stc.state()
        it = st["world"]["items"].get(key) or {}
        worn = it.get("container") is not None and _serial(it["container"]) == me
        if not worn or time.monotonic() > end:
            break
        time.sleep(0.1)
    if worn:
        return out
    try:
        lift, second = combat.equip_packets(st["world"], me, _serial(key))
    except ValueError as e:
        return {**out, "error": str(e)}
    human.wait("use")
    if ctl.send(lift) != "OK":
        return {**out, "error": "lift refused"}
    human.wait("drag")
    if ctl.send(second) != "OK":
        return {**out, "error": "lifted but the equip request was refused; the item may be on the cursor"}
    end = time.monotonic() + EVENT_WAIT_S
    while time.monotonic() < end:
        v = stc.state()["world"]["items"].get(key) or {}
        if v.get("container") is not None and _serial(v["container"]) == me:
            return {**out, "rewielded": True}
        time.sleep(0.1)
    return {**out, "error": "the world model doesn't show it worn again"}


def _act_track(a) -> dict:
    """track <mode> | track off: Tracking's Hunting mode, set the way a player does
    it in the Tracking gump (docs/NOTES.md "Tracking"; tracking.hunt, the same clicks
    the lumber runner uses). Opens the gump with the stock UseSkill (only if it isn't
    open), steps the mode with the gump's arrow buttons (the shorter way round), then
    Begin Hunting. A different mode while hunting: Stop, change, Begin. `off`: Stop
    Hunting (nothing is sent when not hunting). Every click is the stock 0xB1 after a
    reaction pause; the gump stays open, as it does for a player. Hits show in
    `status.tracking` (world model)."""
    if not a.args:
        raise CtlError(f"track <mode> | track off; modes: {', '.join(tracking.MODES)}")
    off = [w.lower() for w in a.args] == ["off"]
    mode = None if off else track_mode(a.args)
    ctl, stc = _connect(a)
    try:
        stc.intent("Tracking: stopping the hunt" if off else f"Tracking: hunting {mode}", "track")
        try:
            res = tracking.hunt(_CtlIO(ctl, stc, tracking.TrackError), mode, Human(a.human, seed=a.seed))
        except tracking.TrackError as e:
            raise CtlError(str(e))
        ok = res["ok"]
        out = {"ok": ok, "reply": ("stopped hunting" if off else f"hunting {mode}") if ok
               else "the server didn't confirm", "clicks": res["clicks"],
               "heard": [journal_view(e) for e in res["events"] if e.get("ev") in JOURNAL_EVS],
               "tracking": {"hunting": res["hunting"], "mode": res["mode"], "arrow": res["arrow"]}}
        if not ok:
            out["error"] = out["reply"]
        return out
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
    pack, s, depth = combat.backpack(items, me), _serial(key), 0
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
        name = item_label(it, amount)
        src, where = _where(items, ikey, me), _where(items, ckey, me)
        human = Human(a.human, seed=a.seed)
        from agent_link import opens_as_container
        opened = _open_first(ctl, stc, human, (item_s, False),
                             (cont_s, opens_as_container(st["world"], cont_s)))
        stc.intent(f"Moving {amount} {name or ikey} from the {src} to the {where}", "store")
        mark = stc.mark()
        resp = ctl.send(actions.lift(item_s, amount))
        if resp != "OK":
            return {"ok": False, "reply": resp}
        human.wait("drag")
        resp = ctl.send(actions.drop(item_s, DROP_AUTO, DROP_AUTO, 0, 0, cont_s))
        if resp != "OK":
            return {"ok": False, "reply": resp,
                    "error": "lifted but the drop was refused; the item may be on the cursor"}

        def moved():
            v = stc.state()["world"]["items"].get(ikey)
            if v is None:                          # merged into a stack there (gold) or used up
                return None                        # (a rune into a book): settled only at the end
            if amount < have:                      # a partial lift leaves the rest behind
                return (v.get("amount") or 1) == have - amount
            return v.get("container") is not None and _serial(v["container"]) == cont_s
        # A refused drop bounces the item back (live 2026-10-02: "You cannot place objects
        # in the book while viewing the contents."), so "gone" only counts once it stays gone.
        end = time.monotonic() + EVENT_WAIT_S
        ok = moved()
        while ok is not True and time.monotonic() < end:
            time.sleep(0.1)
            ok = moved()
        ok = ok is not False
        got = stc.wait_events(mark, lambda evs: False, timeout=0.5)
        out = {"ok": ok, "reply": resp, "moved": ok, "item": name, "amount": amount, "from": src, "into": where,
               "heard": [journal_view(e) for e in got if e.get("ev") in JOURNAL_EVS]}
        if opened:
            out["opened"] = opened
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
        pack = combat.backpack(items, st["movement"].get("self_serial"))
        if pack is None:
            raise CtlError("backpack not known to the world model")
        cands = []
        for k, it in combat.pack_items(items, pack):
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
        opened = _open_first(ctl, stc, Human(a.human, seed=a.seed), (_serial(k), False))
        mark = stc.mark()
        stc.intent(f"Using {name or k}", "use")
        resp = ctl.send(actions.dclick(_serial(k)))
        got = stc.wait_events(mark, lambda evs: any(e.get("ev") == "target" for e in evs), timeout=1.5)
        cur = stc.state()["world"].get("target") or {}
        out = {"ok": resp == "OK", "reply": resp, "used": {"serial": k, "name": name,
                                                            "graphic": f"0x{it['graphic']:04X}",
                                                            "amount": it.get("amount") or 1},
               "cursor": bool(cur.get("active")),
               "heard": [journal_view(e) for e in got if e.get("ev") in JOURNAL_EVS]}
        if opened:
            out["opened"] = opened
        return out
    finally:
        ctl.close()
        stc.close()


def _act_buy(a, mem) -> dict:
    """buy <vendor serial> [ITEM WORDS…] [--amount N]: opens the vendor's Buy
    list through the context menu (like the stock client) and, when an item
    is named, sends the 0x3B buy request for it, checking the policy's daily cap
    (harness/data/policy.json, spends recorded as job events `gold/spend`). Gold
    comes from the pack, and what the pack lacks from the bank account (the
    vendor's "... withdrawn from your bank account." line, BANK_PAID; a bank that
    lacks it too refuses the purchase). Without an item it only returns the price
    list. The price list maps to the vendor container's items in reverse order
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
        for p in context_menu_packets(vendor):
            if ctl.send(p) != "OK":
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
        from_bank = gold_before is not None and total > gold_before   # the vendor withdraws it from the bank
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
        bank = next((int(m.group(1)) for e in got if e.get("ev") == "speech_heard"
                     for m in [BANK_PAID.search(e.get("text") or "")] if m), None)
        if bank is not None:
            paid = (paid or 0) + bank
        out = {"ok": paid is None or paid > 0, "reply": resp, "vendor": vkey, "bought": offer["name"],
               "amount": amount, "price": offer["price"], "total": total, "paid": paid, "from_bank": bank,
               "gold": gold_after, "spent_today": spent + (paid if paid and paid > 0 else 0), "daily_cap": cap,
               "heard": [journal_view(e) for e in got if e.get("ev") in JOURNAL_EVS]}
        if paid is not None and paid > 0:
            mem.job_event("gold", "spend", {"vendor": vkey, "item": offer["name"], "amount": amount,
                                            "price": offer["price"], "total": paid, "from_bank": bank})
        elif paid is not None:
            out["error"] = ("neither your gold nor the bank paid: the purchase probably failed (see heard)"
                            if from_bank else "your gold didn't change: the purchase probably failed (see heard)")
        return out
    finally:
        ctl.close()
        stc.close()


def _act_wear(a) -> dict:
    """unequip <serial>: an item you wear -> your backpack (0x07 lift, pause,
    0x08 drop into the pack). equip <serial>: an item in your backpack (any
    bag depth) -> worn (combat.equip_packets: 0x07 lift, pause, 0x13 equip
    request on its tiledata layer, else the layer the server last wore it on,
    else a known one for its graphic). The stock client's drag sequences; waits
    for the world model to show the move."""
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
        pack = combat.backpack(items, me)
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
            try:
                _lift, second = combat.equip_packets(st["world"], me, serial)
            except ValueError as e:
                raise CtlError(str(e))
            target = me

        def done():
            v = stc.state()["world"]["items"].get(key)
            return v is not None and v.get("container") is not None and _serial(v["container"]) == target
        human = Human(a.human, seed=a.seed)
        # equip lifts from the open bag; unequip drags into the open backpack
        opened = _open_first(ctl, stc, human, (pack, True) if a.name == "unequip" else (serial, False))
        mark = stc.mark()
        resp = ctl.send(actions.lift(serial, it.get("amount") or 1))
        if resp != "OK":
            return {"ok": False, "reply": resp}
        human.wait("drag")
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
        if opened:
            out["opened"] = opened
        if not moved:
            out["error"] = "the world model doesn't show the item moved (check journal/status)"
        return out
    finally:
        ctl.close()
        stc.close()


def gump_reply(state: dict, serial_arg: str, button_arg: str, texts=()) -> bytes:
    """A 0xB1 reply the overseer may send, or CtlError. Like the stock client it
    carries every text entry (current text, or the overseer's --text ID=VALUE)
    and the switches that start checked. Refuses: the captcha (the human's or the runner's solver's),
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
        raise CtlError("that is the captcha: refused (the human solves it, or the runner in captcha mode auto; "
                       "ANTICHEAT.md §8.8)")
    if not view["buttons"]:
        raise CtlError("gump has no reply buttons (decoy/honeypot shape, ANTICHEAT.md §8.13): never reply")
    if any(w in t.lower() for t in view["texts"] for w in RENOUNCE_WORDS) and button != 0:
        raise CtlError("gump mentions renouncing Young status: only closing it (button 0) is allowed; "
                       "leaving Shelter is the human's decision")
    if _serial(g.get("gump_id")) == ROOM_GUMP_ID and ROOM_REFUSED.get(button) in view["texts"]:
        raise CtlError(f"rental room menu: button {button} is '{ROOM_REFUSED[button]}', which changes the rent "
                       "contract: the human's decision")
    if button == 0 and not view["closable"]:
        raise CtlError("gump is noclose; button 0 isn't available")
    if button != 0 and button not in view["buttons"]:
        raise CtlError(f"button {button} not in the gump's reply buttons {view['buttons']}")
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
    return actions.gump_reply(serial, _serial(g.get("gump_id")), button, g.get("layout") or "",
                              g.get("lines") or [], overrides)


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
        key_is_mobile = False
        if isinstance(target, int):
            world = link.state()["world"]
            mob = world["mobiles"].get(key)
            if mob is None:   # out of view: walk to where the client last had it (`ctl npcs`)
                gone = (world.get("last_seen") or {}).get(key)
                if gone and gone.get("why") in ("range", "delete") \
                        and gone.get("facet") == (world.get("self") or {}).get("map"):
                    mob = gone
            item = world["items"].get(key)
            if mob and mob.get("x") is not None:
                radius = 2 if a.range is None else a.range
                if a.z is None and mob.get("z") is not None:
                    z_ok = agent_link.same_floor(mob["z"])

                def center():
                    m = link.state()["world"]["mobiles"].get(key) or mob
                    return (m["x"], m["y"])
                label, text = f"to {key}", f"Walking to {mob.get('name') or key}"
                key_is_mobile = True
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
        guard = None
        if not a.no_guard:
            import travel_guard
            # a mobile target may be the monster we mean to fight: no goal check then
            guard = travel_guard.TravelGuard(mover, mem, goal=None if key_is_mobile else center,
                                             log=agent_link.log)
            mover.guard = guard
        # a goto onto a tile or ground item (range 0) means to use whatever moongate stands on
        # that tile: its gump is left for `act gump`; the gumps of gates the route only passes
        # over are closed by the Mover. Not decided by looking for the gate now: from beyond the
        # view range the world model doesn't have it yet (live 2026-10-03: three gotos onto
        # gates closed the gump on arrival)
        gate = center() if radius == 0 and not key_is_mobile else None
        start = link.pos()
        try:
            mover.walk_to(center, radius, label, max_moves=a.max_moves, z_ok=z_ok, gate=gate)
            ok, err = True, None
        except agent_link.Abort as e:
            ok, err = False, str(e)
        end = link.pos()
        where = text.removeprefix("Walking to ")
        link.intent(f"Arrived at {where}" if ok else f"Stopped walking to {where}: {err}"[:200],
                    "arrived" if ok else "stopped", end[:2], loop="overseer")
    out = {"ok": ok, "from": start, "to": end, "steps": mover.steps, "blocked": mover.blocked_count,
           "doors_opened": mover.doors_opened, "gate_gumps_closed": mover.gate_gumps_closed,
           "avoided": [] if guard is None else [{"x": c[0], "y": c[1], "radius": r} for c, r in mover.danger.values()],
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
    walk = pathfind.Walkers().get(facet, pathfind.ground_items(st["world"]["items"].values()))
    if walk is None:
        raise CtlError(f"no map geometry for facet {facet} (rental rooms are blank; use status/journal)")
    to = None
    if a.to:
        to = (a.to[0], a.to[1])
    return localmap.render(st, walk, radius=max(3, min(a.radius, 30)), to=to, to_z=a.z,
                           to_range=a.range or 0, umap=uomap.UoMap(0 if facet is None else facet))


def _knowledge(mem):
    """Recall is hybrid (meaning + words, bge-small on the GPU via embedder.py) when this
    Python has fastembed, else words only; the model loads only when a search runs."""
    import embedder
    import knowledge
    return knowledge, knowledge.Knowledge(mem.con, embed=embedder if embedder.available() else None)


def _recall_mode(k) -> str:
    """How relevance was ranked: 'hybrid (cuda|cpu)' (meaning + words), 'words' (no
    embedder in this Python), or 'none' (no query words: importance/recency/confidence only)."""
    if k.embed is None:
        return "words"
    return f"hybrid ({k.embed.device()})" if k.embed.loaded() else "none"


def _situation(a, mem) -> dict:
    """The current situation for knowledge recall: the character, position, nearby
    NPCs, open junctures, intent, running task (proxy parts only when it answers)."""
    sit = {"junctures": [f"{j['kind']} {j['summary']}" for j in mem.junctures(open_only=True, limit=10)],
           "task": " ".join(t["task"] for t in running_tasks(mem))}
    try:
        resp = state_query(a.state_port)
    except (OSError, ValueError):
        return sit
    if resp.get("ok"):
        s = summarize(resp)
        sit.update(character=s.get("name"), pos=s["pos"], facet=s["facet"],
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
            verb = {"added": "inscribing", "confirmed": "confirmed", "superseded": "inscribing"}[out["action"]]
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
        elif op in ("pin", "unpin"):
            out = k.pin(a.id, on=op == "pin")
            row = (f"{out['action']} #{a.id}" + (" (recalled at every overseer start)" if op == "pin" else ""),
                   {"id": a.id, "action": out["action"], "entry": _compact(k.get(a.id))})
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
            out = {"results": [kmod._brief(e) | {"status": e["status"]} for e in res], "recall": _recall_mode(k)}
            filters = " ".join(f for f in (f"kind={a.kind}" if a.kind else "",
                                           *(f"tag={t}" for t in (a.tag or ())),
                                           f"near {near[1]},{near[2]}" if near else "") if f)
            row = (f"recalled '{query}'" + (f" ({filters})" if filters else "") + f": {len(res)} result(s)",
                   {"query": query, "filters": filters, "near": list(near) if near else None,
                    "results": [_compact(e) for e in res[:MEMORY_ROW_RESULTS]]})
        elif op == "brief":
            out = k.brief(_situation(a, mem), limit=a.limit) | {"recall": _recall_mode(k)}
            row = (f"briefed: {len(out['pinned'])} pinned, {len(out['relevant'])} relevant, "
                   f"{len(out['standing'])} standing",
                   {"query": out["query"], "near": out["near"],
                    "pinned": [_compact(e) for e in out["pinned"]],
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
    """Mobiles the client has (in_view true, live position) plus the ones it
    dropped (world `last_seen`: left the view range, deleted by the server or
    left behind on another facet; in_view false, where they were last seen and
    `age_s` since): search by name/title words, nearest first. Dead ones are
    left out. NPCs wander, so `goto <serial>` walks to the last-seen spot and
    follows the live position once the mobile is back in view."""
    try:
        stc = StateConn(a.state_port)
    except OSError as e:
        raise CtlError(f"proxy state port {a.state_port} unreachable: {e}")
    try:
        resp = stc.state()
    finally:
        stc.close()
    now = time.time()
    world, mv = resp.get("world") or {}, resp.get("movement") or {}
    pos, me = mv.get("pos"), mv.get("self_serial")
    labels = world.get("labels") or {}
    words = [w.lower() for w in a.words]
    rows = []
    live = world.get("mobiles") or {}
    gone = {k: m for k, m in (world.get("last_seen") or {}).items() if m.get("why") != "dead" and k not in live}
    for in_view, src in ((True, live), (False, gone)):
        for key, m in src.items():
            if m.get("x") is None or _serial(key) == me:
                continue
            label = labels.get(key) or m.get("name") or ""
            if words and not all(w in label.lower() for w in words):
                continue
            dist = nav.chebyshev((m["x"], m["y"]), (pos[0], pos[1])) if pos else None
            row = {"serial": key, "label": label or None, "name": m.get("name"),
                   "notoriety": m.get("notoriety"), "notoriety_name": NOTORIETY.get(m.get("notoriety")),
                   "x": m["x"], "y": m["y"], "z": m.get("z"), "dist": dist, "in_view": in_view,
                   "age_s": _age(now, m.get("seen_t") if in_view else m.get("t"))}
            if not in_view:
                row.update(why=m.get("why"), facet=m.get("facet"))
            rows.append(row)
    rows.sort(key=lambda r: (r["dist"] is None, r["dist"] or 0, not r["in_view"]))
    return {"ok": True, "pos": pos, "matches": len(rows), "npcs": rows[:a.limit],
            "note": "in_view = the client has it (live position, age_s since the last update); "
                    "others are where the client last had them (age_s since; why: range = left the view, "
                    "delete = removed by the server, facet = on another facet)"}


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


def cmd_runes(a, mem):
    """Rune libraries (harness/places.py, harness/data/rune_libraries.json): `libraries`
    lists them; `find WORDS…` and `near X Y` say which tome's rune gets you where, with
    the command that recalls with it once you stand by that tome. `near` also lists
    the runes of the character's own books (`act recall --check` remembers them;
    character: --character, else the logged-in one, else every character's), each
    row with its `source` (library|book) and `danger` (places.landings). Reading a
    library is the act `read_tomes`."""
    import places

    def row(r):
        if r.get("source") == "book":
            return {"source": "book", "book": r["book"], "kind": r["kind"], "book_title": r["book_title"],
                    "name": r["name"], "tile": [r["x"], r["y"]], "dist": r["dist"], "danger": r["danger"],
                    "recall": f"ctl act recall {r['book']} --rune \"{r['name'].strip()}\""}
        out = {"library": r["library"], "name": r["name"], "tile": [r["x"], r["y"]], "tome": r["tome"],
               "tome_title": r["tome_title"], "tome_pos": r["tome_pos"][:2]}
        if "source" in r:
            out = {"source": r["source"], **out, "danger": r["danger"]}
        if r["witcher"]:
            out["witcher"] = r["witcher"]
            out["witcher_name"] = places.witcher_rune(r["witcher"])["name"]
            out["recall"] = f"ctl act recall --library {r['library']} --witcher {r['witcher']}"
        else:
            out["recall"] = f"ctl act recall --library {r['library']} --rune \"{r['name'].strip()}\""
            for tome in (None, r["tome_title"], r["tome"]):      # the shortest selector that finds this row
                try:
                    if places.library_rune(r["library"], r["name"], tome)["tome"] == r["tome"]:
                        if tome:
                            out["recall"] += f" --tome \"{tome}\""
                        break
                except KeyError:
                    continue
        if "dist" in r:
            out["dist"] = r["dist"]
        return out
    try:
        if a.runes_op == "libraries":
            return {"ok": True, "libraries": [
                {"id": lb["id"], "name": lb["name"], "access": lb.get("access"), "facet": lb["facet"],
                 "stand": lb["stand"], "use_range": lb["use_range"], "runes": sum(len(t["rows"]) for t in lb["tomes"]),
                 "tomes": [f"{t['title']} ({len(t['rows'])})" for t in lb["tomes"]]} for lb in places.libraries()]}
        if a.runes_op == "find":
            hits = places.find_runes(a.words, a.library)
            if not hits:        # a Witcher rune by number or by its table name
                ws = [w.lower() for w in a.words]
                ids = {r["id"] for r in places.witcher()["runes"]
                       if ws == [r["id"]] or all(w in r["name"].lower() for w in ws)}
                hits = [r for r in places.runes(a.library) if r["witcher"] in ids]
            return {"ok": True, "found": len(hits), "runes": [row(r) for r in hits[:a.limit]]}
        if a.runes_op == "near":
            character = a.character
            if character is None:
                try:
                    resp = state_query(a.state_port)
                except (OSError, ValueError):
                    resp = {}
                character = ((resp.get("world") or {}).get("self") or {}).get("name") if resp.get("ok") else None
            libs = [a.library] if a.library else [lb["id"] for lb in places.libraries()]
            rows = places.landings(a.x, a.y, a.facet, libraries=libs, books=places.known_books(mem, character),
                                   include_dangerous=True, limit=a.limit)
            return {"ok": True, "to": [a.x, a.y], "character": character, "runes": [row(r) for r in rows]}
    except KeyError as e:
        raise CtlError(e.args[0])
    raise CtlError(f"unknown runes op {a.runes_op}")


def cmd_lumber(a, mem):
    """The self-optimizing lumber job (harness/lumber_opt.py, docs/LUMBER_LOOP.md §6):
    `plan` picks the spot (Thompson sampling), trip size and hatchet for the next
    `run lumber`; `spots`, `spot add|set`, `discover` manage the spots; `price`
    and `prices` keep observed market prices."""
    import lumber_opt
    heartbeat(mem)
    op = a.lumber_op
    if op == "plan":
        world = serial = pos = facet = None
        try:
            resp = state_query(a.state_port)
        except (OSError, ValueError):
            resp = {}
        if resp.get("ok"):
            world, mv = resp.get("world") or {}, resp.get("movement") or {}
            serial, pos = mv.get("self_serial"), mv.get("pos")
            facet = (world.get("self") or {}).get("map")
        # the character (world self name) picks its home (harness/data/homes.json) and books;
        # Young by the "(young)" name label (lumber_opt.character): a death loses nothing
        out = lumber_opt.plan_from_store(mem, world, serial, pos, facet, stint_min=a.stint_min, seed=a.seed)
        out["proxy"] = bool(resp.get("ok"))
        if not a.all:
            out["spots"] = [r for r in out["spots"] if r["status"] == "active"]
            out["regrow"] = {k: v for k, v in out["regrow"].items() if k != "curve"}
        p = out.get("pick")
        text = (f"lumber plan: {p['spot']} ({p['mode']}, P(best) {p['p_best']:.2f}, ~{p['expected_net_logs_h']} "
                f"logs/h, landing {(p.get('landing') or {}).get('name')}) -> {p['command']}") if p \
            else f"lumber plan: {out.get('error')}"
        mem.chat_post("overseer", text, "action", data={"cmd": "lumber plan", "pick": p})
        return out
    if op == "spots":
        spots = lumber_opt.load_spots(mem)
        return {"ok": True, "spots": [
            {k: s.get(k) for k in ("id", "name", "status", "reason", "source", "facet", "area", "pvp",
                                   "hazard_prior", "danger_hint", "route_tiles", "tree_count")}
            for s in sorted(spots.values(), key=lambda s: (s["status"] != "active", s["id"]))]}
    if op == "spot" and a.spot_op == "add":
        spots = lumber_opt.load_spots(mem)
        if a.id in spots:
            raise CtlError(f"spot {a.id} exists; use `lumber spot set`")
        spot = {"id": a.id, "name": a.name or a.id, "facet": a.facet,
                "area": {"center": list(a.center), "radius": a.radius, "note": a.note or ""},
                "trees": [], "pvp": not a.no_pvp, "hazard_prior": a.hazard_prior}
        try:
            lumber_opt.check_spot(spot)
        except ValueError as e:
            raise CtlError(str(e))
        mem.lumber_spot_put(a.id, a.status, spot, "overseer")
        mem.chat_post("overseer", f"lumber spot add {a.id} ({a.status})", "action",
                      data={"cmd": "lumber spot add", "spot": spot})
        return {"ok": True, "spot": {**spot, "status": a.status}}
    if op == "spot" and a.spot_op == "set":
        spots = lumber_opt.load_spots(mem)
        if a.id not in spots:
            raise CtlError(f"unknown spot {a.id}")
        row = next((r for r in mem.lumber_spot_rows() if r["id"] == a.id), None)
        data = row["data"] if row else {}
        mem.lumber_spot_put(a.id, a.status, data, row["source"] if row else "overseer", a.reason)
        mem.chat_post("overseer", f"lumber spot set {a.id} {a.status}" + (f" ({a.reason})" if a.reason else ""),
                      "action", data={"cmd": "lumber spot set", "id": a.id, "status": a.status})
        return {"ok": True, "id": a.id, "status": a.status, "reason": a.reason}
    if op == "discover":
        import pathfind
        import places
        import uomap
        umap = uomap.UoMap(a.facet)
        walk = pathfind.Walk(umap)
        route_fn = None if a.no_route_check else lumber_opt.make_route_fn(walk)
        all_spots = lumber_opt.load_spots(mem)
        # A new discovery replaces the earlier candidates nobody has acted on;
        # approved (active) and disabled spots stay and keep their ground.
        replaced = {sid for sid, s in all_spots.items()
                    if s["status"] == "candidate" and s.get("source") == "discover"}
        spots = {sid: s for sid, s in all_spots.items() if sid not in replaced}
        found, skipped = lumber_opt.discover_witcher(
            umap.find_trees, places.witcher_runes_in(a.library), spots, library=a.library, radius=a.radius,
            search=lumber_opt.RUNE_SEARCH if a.search is None else a.search, min_trees=a.min_trees,
            max_route=lumber_opt.MAX_RUNE_ROUTE if a.max_route is None else a.max_route, route_fn=route_fn,
            include_dangerous=a.include_dangerous,
            towns=[(t["x"], t["y"]) for t in places.atlas(("town",)) if t["facet"] == a.facet],
            guard_points=mem.guard_points(a.facet))
        found.sort(key=lambda s: -s["tree_count"])
        if not a.dry_run:
            for sid in replaced - {s["id"] for s in found}:
                mem.lumber_spot_delete(sid)
            for s in found:
                mem.lumber_spot_put(s["id"], "candidate", s, "discover")
            mem.chat_post("overseer", f"lumber discover ({a.library}): {len(found)} candidate spot(s), "
                                      f"replacing {len(replaced)}", "action",
                          data={"cmd": "lumber discover", "ids": [s["id"] for s in found]})
        return {"ok": True, "library": a.library,
                "candidates": found, "left_out": skipped, "replaced": len(replaced), "saved": not a.dry_run,
                "next": "inspect each with `ctl map` near its area (no dungeon, outside town), then "
                        "`ctl lumber spot set <id> --status active` or `--status disabled --reason ...`"}
    if op == "price":
        pid = mem.price_record(a.item, a.gp, a.source, a.note)
        mem.chat_post("overseer", f"price {a.item} = {a.gp:g} gp ({a.source})", "action",
                      data={"cmd": "lumber price", "item": a.item, "gp": a.gp, "id": pid})
        return {"ok": True, "id": pid, "item": a.item, "price_gp": a.gp}
    if op == "prices":
        return {"ok": True, "prices": mem.prices()}
    raise CtlError(f"unknown lumber op {op}")


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
    sub.add_parser("break").set_defaults(fn=cmd_break)
    p = sub.add_parser("alert")
    p.add_argument("reason", nargs="+")
    p.add_argument("--serial", default=None)
    p.set_defaults(fn=cmd_alert)
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
    p.add_argument("--after", type=int, default=None, help="only ids above this, oldest first "
                   "(default: the newest --limit)")
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
    p.add_argument("--walk", action="store_true",
                   help="walk: walk instead of run (the client's Always Run is on, so the default is run)")
    p.add_argument("--human", choices=sorted(PROFILES), default="normal", help="walk pacing profile")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--gheal-min-missing", type=int, default=None,
                   help="heal: missing hits from which the spell is Greater Heal, not Heal "
                        "(default: the mana break-even for your Magery, 19 at Magery 60)")
    p.add_argument("--check", action="store_true",
                   help="recall: only open the book and read it (default rune, charges; your own book: every "
                        "rune's name and tile, remembered for this character)")
    p.add_argument("--rune", default=None,
                   help="recall: a rune of your runebook or rune tome by name instead of the default (with "
                        "--library: a library row)")
    p.add_argument("--witcher", default=None,
                   help="recall: Witcher rune N from a rune library's tome (stand by it; the library you stand "
                        "in, else --library, else Cambria)")
    p.add_argument("--library", default=None,
                   help="recall: the rune library (harness/data/rune_libraries.json: cambria, dtf) for --witcher/--rune")
    p.add_argument("--tome", default=None,
                   help="recall --library --rune: the tome (title or serial) when several tomes have that name")
    p.add_argument("--range", type=int, default=None,
                   help="goto: stop within this many tiles (default 0 for a tile, 2 for a mobile)")
    p.add_argument("--max-moves", type=int, default=None,
                   help="goto: abort after this many steps (default: no limit)")
    p.add_argument("--no-guard", action="store_true",
                   help="goto: walk without the travel guard (travel_guard.py: route around hostile creatures, "
                        "stop on a hostile player, low hits under attack, death or a goal inside a monster's "
                        "reach); only to walk into a fight on purpose")
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
    p = sub.add_parser("runes", help="rune libraries: which tome's rune gets you where (harness/places.py)")
    rs = p.add_subparsers(dest="runes_op", required=True)
    rs.add_parser("libraries", help="every library: where to stand, its tomes")
    q = rs.add_parser("find", help="library runes whose name holds every word, e.g. bank prevalia, dock")
    q.add_argument("words", nargs="+")
    q.add_argument("--library", default=None, help="only this library (cambria, dtf)")
    q.add_argument("--limit", type=int, default=30)
    q = rs.add_parser("near", help="the library runes and your own books' runes that land nearest a tile")
    q.add_argument("x", type=int)
    q.add_argument("y", type=int)
    q.add_argument("--facet", type=int, default=0)
    q.add_argument("--library", default=None, help="only this library's rows (cambria, dtf); own books still listed")
    q.add_argument("--character", default=None,
                   help="whose remembered books (default: the logged-in character, else every character's)")
    q.add_argument("--limit", type=int, default=8)
    p.set_defaults(fn=cmd_runes)
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
    _lumber_parser(sub)
    return ap


def _lumber_parser(sub):
    p = sub.add_parser("lumber", help="self-optimizing lumber job: plan, spots, discover, prices")
    ls = p.add_subparsers(dest="lumber_op", required=True)
    q = ls.add_parser("plan", help="where to chop next (and the landing rune out), how much per trip, which "
                                   "hatchet (Thompson sampling); trips start and end at the character's home")
    q.add_argument("--stint-min", type=float, default=60.0,
                   help="how long this run should last (sets --trips; a longer trip runs once)")
    q.add_argument("--seed", type=int, default=None, help=argparse.SUPPRESS)
    q.add_argument("--all", action="store_true", help="also candidate/disabled spots and the regrowth curve")
    ls.add_parser("spots", help="every spot with its status")
    q = ls.add_parser("spot")
    ss = q.add_subparsers(dest="spot_op", required=True)
    r = ss.add_parser("add", help="a new spot: a tree area (trips reach it from home by the nearest landing rune)")
    r.add_argument("id")
    r.add_argument("--name")
    r.add_argument("--center", type=int, nargs=2, required=True, metavar=("X", "Y"))
    r.add_argument("--radius", type=int, required=True)
    r.add_argument("--facet", type=int, default=0)
    r.add_argument("--no-pvp", action="store_true", help="no hostile player actions there (no recall needed)")
    r.add_argument("--hazard-prior", type=float, default=0.5, help="expected hostile players seen per field hour")
    r.add_argument("--note")
    r.add_argument("--status", choices=("active", "candidate"), default="active")
    r = ss.add_parser("set", help="approve (active), park (candidate) or disable a spot")
    r.add_argument("id")
    r.add_argument("--status", choices=("active", "candidate", "disabled"), required=True)
    r.add_argument("--reason")
    q = ls.add_parser("discover", help="propose tree-dense areas near Witcher runes as candidate spots (map data)")
    q.add_argument("--library", default="cambria", help="the rune library whose Witcher runes to search around")
    q.add_argument("--include-dangerous", action="store_true",
                   help="also runes named after monster places (Brigand Camp, Orc Fort, ...)")
    q.add_argument("--facet", type=int, default=0)
    q.add_argument("--radius", type=int, default=14)
    q.add_argument("--min-trees", type=int, default=25)
    q.add_argument("--search", type=int, default=None,
                   help="grove window centres up to this many tiles from the rune (default 200)")
    q.add_argument("--max-route", type=int, default=None,
                   help="the longest walk (tiles) from the rune's landing into its grove (default 300)")
    q.add_argument("--no-route-check", action="store_true", help="skip the walking-route check (faster)")
    q.add_argument("--dry-run", action="store_true", help="list, don't save")
    q = ls.add_parser("price", help="record an observed price, e.g. hatchet:copper 1200, board:ordinary 9, "
                                    "reagent:black_pearl 5 or recall_charge 30 (supplies: what a trip's recalls cost)")
    q.add_argument("item")
    q.add_argument("gp", type=float)
    q.add_argument("--source", default="observed", help="where: vendor search, NPC, player, chat#")
    q.add_argument("--note")
    ls.add_parser("prices", help="the newest price per item")
    p.set_defaults(fn=cmd_lumber)


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
    for name, text in (("pin", "make an entry a standing memory `know brief` always returns (overseer start)"),
                       ("unpin", "stop returning an entry from every `know brief`")):
        ks.add_parser(name, help=text).add_argument("id", type=int)
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

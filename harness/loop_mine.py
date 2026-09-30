"""Demonstration miner (docs/LUMBER_LOOP.md §4, milestone M0).

    python harness/loop_mine.py timeline <TAG> [--labels] [--cliloc PATH]

Replays logs/session_<TAG>.* through the proxy's own SessionTap
(viz_feed.ReplayDriver, the same pipeline the live state port serves) and
prints a chronological, human-readable timeline of what the player did and
what the server answered:

- walks collapsed into `walk (x,y) -> (x,y), N steps [, D denied]`
- double clicks, lifts, drops, equips, target cursors and responses, speech,
  each with the entity described (serial, name/label, graphic, container)
- gumps: id, serial, reply-button ids, text-entry ids, text lines, cliloc text
- gump responses: button, switches, entered text
- cliloc and system messages, rendered from Cliloc.enu (number kept)
- amount changes in the backpack, the bank box and every opened container
- buffs added/removed
- agent-sent packets (proxy origin)

and, at the end, every serial the player acted on plus the packet ids the
world model does not parse yet. The timeline is the evidence from which the
loop knowledge file (harness/data/loops/lumber.json) is written after the
user's demonstration run.

Reads captures and Cliloc.enu only; sends nothing anywhere.
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_feed  # noqa: E402
from nav import DIR_DELTA  # noqa: E402
from uo import cliloc  # noqa: E402
from uo.gumps import parse_layout  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHUNK = 50               # replay items applied between event drains
LAYER_BACKPACK = 0x15
LAYER_BANK = 0x1D
# world events that carry no player-level information for the timeline
SKIP = {"keepalive", "query", "item_query", "names", "item_seen", "animation",
        "walk", "container_content", "census", "dialect_handshake"}


class Timeline:
    def __init__(self, tag, logdir, table, labels=False, out=sys.stdout):
        self.drv = viz_feed.ReplayDriver(tag, logdir)
        self.table = table
        self.show_labels = labels
        self.out = out
        self.cursor = 0
        self.desc_cache = {}                     # serial -> last known description
        self.acted = collections.OrderedDict()   # serial -> set of actions
        self.walk = None                         # [t, start, end, steps, denies]
        self.amounts = {}                        # (container, graphic, hue) -> amount

    # ---------------------------------------------------------------- helpers
    @property
    def state(self):
        return self.drv.tap.world.state

    def _ts(self, t):
        dt = max(0.0, t - self.drv.t0)
        return f"{int(dt // 60):3d}:{dt % 60:04.1f}"

    def _pos(self):
        s = self.state.self
        return f"({s.x},{s.y})" if s.position_absolute else "(?)"

    def emit(self, t, text):
        self._flush_walk()
        print(f"[{self._ts(t)}] {self._pos():>12} {text}", file=self.out)

    def _refresh_cache(self):
        st = self.state
        for s, m in st.mobiles.items():
            self.desc_cache[s] = self._describe_mobile(s, m)
        for s, it in st.items.items():
            self.desc_cache[s] = self._describe_item(s, it)

    def _describe_mobile(self, s, m):
        name = self.state.labels.get(s) or m.name or self.state.names.get(s)
        return f"mobile 0x{s:08X} {name!r} body 0x{(m.graphic or 0):04X}"

    def _describe_item(self, s, it):
        name = self.state.labels.get(s) or it.name or self.state.names.get(s)
        where = self._where(it.container, it.layer)
        amt = f" x{it.amount}" if it.amount and it.amount > 1 else ""
        return f"item 0x{s:08X} {name!r} g0x{(it.graphic or 0):04X} hue {it.hue or 0}{amt} {where}"

    def _where(self, container, layer):
        st = self.state
        if container is None:
            return "on ground"
        if container == st.self.serial:
            return f"worn layer 0x{(layer or 0):02X}"
        special = {self._special(LAYER_BACKPACK): "in backpack",
                   self._special(LAYER_BANK): "in bank box"}
        return special.get(container, f"in 0x{container:08X}")

    def _special(self, layer):
        st = self.state
        for s, it in st.items.items():
            if it.container == st.self.serial and it.layer == layer:
                return s
        return None

    def desc(self, serial):
        if serial is None:
            return "none"
        st = self.state
        if serial == st.self.serial:
            return "self"
        if serial in st.mobiles:
            return self._describe_mobile(serial, st.mobiles[serial])
        if serial in st.items:
            return self._describe_item(serial, st.items[serial])
        return self.desc_cache.get(serial, f"0x{serial:08X} (unseen)")

    def _acted(self, serial, what):
        if serial not in (None, 0, 0xFFFFFFFF):
            self.acted.setdefault(serial, set()).add(what)

    def _text(self, number, args=""):
        return f"#{number} {cliloc.translate(self.table, number, args)!r}" if self.table \
            else f"#{number} args {args!r}"

    # ------------------------------------------------------------ walk merge
    def _flush_walk(self):
        w, self.walk = self.walk, None
        if w and (w[3] or w[4]):
            t, a, b, steps, denies = w
            den = f", {denies} denied" if denies else ""
            print(f"[{self._ts(t)}] {'':>12} walk ({a[0]},{a[1]}) -> ({b[0]},{b[1]}), "
                  f"{steps} steps{den}", file=self.out)

    def _walk(self, t, d, denied=False):
        xy = (d["x"], d["y"])
        if self.walk is None:
            start = xy
            if d.get("moved"):          # the event carries the post-step position
                dx, dy = DIR_DELTA[d["direction"] & 7]
                start = (xy[0] - dx, xy[1] - dy)
            self.walk = [t, start, xy, 0, 0]
        self.walk[2] = xy
        if denied:
            self.walk[4] += 1
        elif d.get("moved"):
            self.walk[3] += 1

    # --------------------------------------------------------- amount diffs
    def _amount_diffs(self, t):
        st = self.state
        watched = {self._special(LAYER_BACKPACK), self._special(LAYER_BANK)} | set(st.containers)
        watched.discard(None)
        now = collections.Counter()
        for it in st.items.values():
            if it.container in watched and it.graphic is not None:
                now[(it.container, it.graphic, it.hue or 0)] += it.amount or 1
        for key in sorted(set(now) | set(self.amounts)):
            before, after = self.amounts.get(key, 0), now.get(key, 0)
            if before != after:
                c, g, h = key
                name = next((self.state.names.get(s) or it.name for s, it in st.items.items()
                             if it.container == c and it.graphic == g), None)
                self.emit(t, f"amount {self._where(c, None)} g0x{g:04X} hue {h} {name!r}: "
                             f"{before} -> {after} ({after - before:+d})")
        self.amounts = dict(now)

    # --------------------------------------------------------------- events
    def on_event(self, env):
        t, d = env["t"], env["data"]
        ev = d.get("ev")
        if env.get("origin") == "proxy":
            if ev == "c2s" and d.get("src") != "client" and d.get("id") != "0x02":
                self.emit(t, f"agent sent {d.get('id')}")   # agent walks show as walks
            return
        if ev in SKIP:
            return
        if ev == "walk_confirm":
            return self._walk(t, d)
        if ev == "walk_deny":
            return self._walk(t, d, denied=True)
        if ev == "dclick":
            self._acted(d["serial"], "dclick")
            return self.emit(t, f"dclick {self.desc(d['serial'])}")
        if ev == "lift":
            self._acted(d["serial"], "lift")
            return self.emit(t, f"lift {d['amount']} of {self.desc(d['serial'])}")
        if ev == "drop":
            self._acted(d["serial"], "drop")
            dest = ("ground" if d["container"] == 0xFFFFFFFF
                    else self.desc(d["container"]))
            self._acted(d["container"], "drop_into")
            return self.emit(t, f"drop 0x{d['serial']:08X} -> {dest} at "
                                f"({d['x']},{d['y']},{d['z']}) grid {d['grid']}")
        if ev == "equip_request":
            self._acted(d["serial"], "equip")
            return self.emit(t, f"equip {self.desc(d['serial'])} layer 0x{d['layer']:02X}")
        if ev == "target":
            return self.emit(t, f"TARGET CURSOR id 0x{d['cursor_id']:08X} "
                                f"type {d['target_type']} ctype {d['cursor_type']}")
        if ev == "target_response":
            self._acted(d["serial"], "target")
            what = self.desc(d["serial"]) if d["serial"] else f"location g0x{d['graphic']:04X}"
            return self.emit(t, f"target -> {what} at ({d['x']},{d['y']},{d['z']}) "
                                f"cursor 0x{d['cursor_id']:08X}")
        if ev == "gump_open":
            return self._gump(t, d)
        if ev == "gump_response":
            texts = [(x["id"], x["text"]) for x in d.get("texts", [])]
            return self.emit(t, f"gump reply serial 0x{d['serial']:08X} gump 0x{d['gump_id']:08X} "
                                f"button {d['button_id']} switches {d.get('switches', [])} "
                                f"texts {texts}")
        if ev == "speech":
            kw = f" keywords {d['keywords']}" if d.get("keywords") else ""
            return self.emit(t, f"SAY {d['text']!r}{kw}")
        if ev == "speech_heard":
            if d["type"] == 6 and not self.show_labels:
                return
            who = "system" if d["serial"] in (0, 0xFFFFFFFF) else self.desc(d["serial"])
            return self.emit(t, f"heard type {d['type']} from {who}: {d['text']!r}")
        if ev == "cliloc":
            who = "system" if d["serial"] in (0, 0xFFFFFFFF) else self.desc(d["serial"])
            if d["type"] == 6 and not self.show_labels:
                return
            affix = f" affix {d['affix']!r}" if d.get("affix") else ""
            return self.emit(t, f"cliloc type {d['type']} from {who}: "
                                f"{self._text(d['cliloc'], d['args'])}{affix}")
        if ev == "container_open":
            self._acted(d["serial"], "open")
            return self.emit(t, f"container open {self.desc(d['serial'])} gump 0x{d['gump_id']:04X}")
        if ev == "buff_update":
            return self.emit(t, f"buff on 0x{d['serial']:08X}: icon {d['icon_id']} {d.get('title')!r}")
        if ev == "buff_remove":
            return self.emit(t, f"buff off 0x{d['serial']:08X}: {d['buff_id']}")
        if ev == "buy_list":
            items = ", ".join(f"{i['name']} {i['price']}gp" for i in d["items"])
            return self.emit(t, f"VENDOR buy list 0x{d['container']:08X}: {items}")
        if ev == "buy":
            self._acted(d["vendor"], "buy_from")
            items = ", ".join(f"{i['amount']} x {self.desc(i['serial'])}" for i in d["items"])
            return self.emit(t, f"BUY from {self.desc(d['vendor'])}: {items or 'nothing'}")
        if ev == "command":
            return self.emit(t, f"command 0x{d['type']:02X} {d['text']!r}")
        if ev == "popup":
            entries = ", ".join(f"{e['index']}:{self._text(e['cliloc'])}" for e in d["entries"])
            return self.emit(t, f"CONTEXT MENU of {self.desc(d['serial'])}: {entries}")
        if ev == "popup_request":
            self._acted(d["serial"], "context_menu")
            return self.emit(t, f"context menu request {self.desc(d['serial'])}")
        if ev == "popup_select":
            return self.emit(t, f"context menu select index {d['index']} on {self.desc(d['serial'])}")
        if ev == "delete":
            return None
        fields = {k: v for k, v in d.items() if k != "ev"}
        return self.emit(t, f"{ev} {fields}")

    def _gump(self, t, d):
        g = parse_layout(d.get("layout", ""))
        lines = d.get("lines") or []
        texts = list(dict.fromkeys(lines[i] for i in g["line_refs"] if 0 <= i < len(lines)))
        clis = [cliloc.translate(self.table, n) if self.table else f"#{n}"
                for n in g["clilocs"]]
        self.emit(t, f"GUMP open serial 0x{d['serial']:08X} gump 0x{d['gump_id']:08X} "
                     f"buttons {g['buttons']} entries {g['entries']}")
        for s in (texts + clis)[:20]:
            print(f"{'':>30}| {s[:160]!r}", file=self.out)

    # ------------------------------------------------------------------ run
    def run(self):
        drv = self.drv
        total = len(drv.items)
        applied = 0
        while drv.position < total:
            drv._advance(None, 1)      # one packet, then its events: exact positions
            applied += 1
            tap = drv.tap
            start = max(self.cursor - tap.events_base, 0)
            fresh = tap.events[start:]
            self.cursor = tap.events_base + len(tap.events)
            for env in fresh:
                self.on_event(env)
            if applied % CHUNK == 0 or drv.position >= total:
                self._amount_diffs(drv.now)
                self._refresh_cache()
        self._flush_walk()
        self.summary()

    def summary(self):
        p = lambda s="": print(s, file=self.out)  # noqa: E731
        p()
        p(f"== order {self.drv.order}" + (f" ({self.drv.order_note})" if self.drv.order_note else ""))
        p("== entities acted on")
        for s, acts in self.acted.items():
            p(f"  {', '.join(sorted(acts)):28} {self.desc(s)}")
        w = self.drv.tap.world
        p("== packets the world model does not parse (dir, id, count)")
        for (d, pid), n in w.unhandled.most_common():
            p(f"  {d} 0x{pid:02X} {n}")
        if w.dialect_unhandled:
            p("== unhandled 0xFF dialect subs")
            for (d, sub), n in w.dialect_unhandled.most_common():
                p(f"  {d} sub 0x{sub:X} {n}")
        p(f"== parse failures {w.parse_failures}, anomalies {dict(w.anomalies)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    tl = sub.add_parser("timeline", help="chronological digest of a capture")
    tl.add_argument("tag", help="capture tag, e.g. 20260929_163420")
    tl.add_argument("--logdir", default=os.path.join(ROOT, "logs"))
    tl.add_argument("--labels", action="store_true", help="also print click labels (type 6)")
    tl.add_argument("--cliloc", default=cliloc.CLILOC_PATH,
                    help="Cliloc.enu to render messages with ('' = numbers only)")
    a = ap.parse_args(argv)
    table = cliloc.load(a.cliloc) if a.cliloc else None
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    Timeline(a.tag, a.logdir, table, labels=a.labels).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

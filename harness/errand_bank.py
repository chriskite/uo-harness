"""Unattended bank run: the Phase 3 closed-loop acceptance task (docs/PLAN.md).

  1. (optional --start x,y) walk to the start tile
  2. find a banker: a "... the banker" label already heard, else single-click
     nearby human NPCs (nearest first, human pace) until one answers with one
  3. walk to within --range tiles of the banker (RunUO banker speech range 12)
  4. say "bank" (keyword-encoded exactly like the stock client) and verify the
     server opened the player's bank box (0x24 on the layer-0x1D item)
  5. walk back to the start tile and verify the position

Feedback comes from the proxy's state port (server-true position, live world
model), actions go through the control port. Walking uses walk memory
(harness/nav.py): known-walkable ground from past captures. Server-denied moves
are learned and trigger a replan; the updated memory is saved.

Guards: jittered human pacing, --timeout overall cap, abort on a movement
stall, hit-point loss, or an assistant-restriction system message
(ANTICHEAT.md §8 rule 4). The only thing ever said in game is "bank".

Run:  python harness/errand_bank.py [--start 1963,2597] [--range 8]
"""
import argparse
import sys
import time

sys.path.insert(0, r"C:/Users/chris/uo-harness/harness")
import actions  # noqa: E402
import nav  # noqa: E402
from agent_link import Abort, Link, Mover, cheb, log, serial_of  # noqa: E402
from humanize import PROFILES, Human  # noqa: E402

MEMORY_PATH = r"C:/Users/chris/uo-harness/harness/data/walkmem.json"
BANKBOX_LAYER = 0x1D
HUMAN_BODIES = (0x190, 0x191)
GATING_WORDS = ("razor", "assistant", "macro", "script")


class Errand:
    def __init__(self, link: Link, memory: nav.WalkMemory, args):
        self.link = link
        self.args = args
        self.deadline = time.monotonic() + args.timeout
        self.start_hits = None
        self.heard_upto = 0
        self.human = Human(args.human, seed=args.seed, log=log)
        self.mover = Mover(link, memory, self.human, max_blocked=args.max_blocked,
                           guard=self.check_guards, use_map=not args.no_map)

    # ---- guards ----
    def check_guards(self, st: dict):
        if time.monotonic() > self.deadline:
            raise Abort(f"overall timeout ({self.args.timeout}s)")
        mv = st["movement"]
        if mv["stalled"]:
            raise Abort(f"movement stalled ({mv['rejects_in_row']} walks rejected in a row)")
        me = st["world"]["self"]
        hits = me.get("hits")
        if hits is not None:
            if self.start_hits is None:
                self.start_hits = hits
            elif hits < self.start_hits:
                raise Abort(f"hit points dropped ({self.start_hits} -> {hits}); stopping")
        for ev in self.link.events[self.heard_upto:]:
            if ev.get("ev") == "speech_heard" and ev.get("serial") in (None, 0, 0xFFFFFFFF, "0xFFFFFFFF"):
                text = (ev.get("text") or "").lower()
                if any(w in text for w in GATING_WORDS):
                    raise Abort(f"server restriction message: {ev.get('text')!r}")
        self.heard_upto = len(self.link.events)

    def pos(self, st=None):
        return self.link.pos(st)

    def look_at(self, serial: int, known_name: bool):
        """Single-click an entity exactly like the stock client: 0x09, then
        0x34 status request, plus 0x98 name request when the name is unknown."""
        self.link.send(actions.single_click(serial))
        self.link.send(actions.status_request(serial))
        if not known_name:
            self.link.send(actions.name_request(serial))

    # ---- banker ----
    def find_banker(self):
        st = self.link.state()
        mobiles = st["world"]["mobiles"]
        known = self._banker_from_labels(st["world"])
        if known:
            return known
        me = tuple(self.pos(st)[:2])
        cands = []
        for key, m in mobiles.items():
            if m.get("x") is None or m.get("graphic") not in HUMAN_BODIES:
                continue
            serial = serial_of(key)
            if serial == st["movement"]["self_serial"]:
                continue
            dist = cheb(me, (m["x"], m["y"]))
            if dist <= self.args.search_radius:
                cands.append((dist, serial, m.get("name")))
        cands.sort()
        log(f"banker search: {len(cands)} nearby NPCs to look at")
        for dist, serial, name in cands[: self.args.max_clicks]:
            self.human.wait("use")
            mark = len(self.link.events)
            self.look_at(serial, known_name=bool(name))
            st = self.link.wait(lambda s: self._label_for(serial, mark) is not None, timeout=1.5)
            label = self._label_for(serial, mark)
            log(f"  looked at {name or hex(serial)} ({dist} tiles): {label!r}")
            if label and "banker" in label.lower():
                m = self.link.state()["world"]["mobiles"].get(f"0x{serial:08X}") or {}
                return serial, label, (m.get("x"), m.get("y"))
        raise Abort("no banker found nearby")

    def _label_for(self, serial: int, since_idx: int):
        for ev in self.link.events[since_idx:]:
            if ev.get("ev") == "speech_heard" and ev.get("serial") in (serial, f"0x{serial:08X}"):
                return ev.get("text")
        return None

    def _banker_from_labels(self, world):
        """A positioned mobile whose latest click label (world.labels) names it a banker."""
        for key, text in world.get("labels", {}).items():
            m = world["mobiles"].get(key)
            if "the banker" in text.lower() and m and m.get("x") is not None:
                return serial_of(key), text, (m["x"], m["y"])
        return None

    def banker_pos(self, serial: int, fallback):
        m = self.link.state()["world"]["mobiles"].get(f"0x{serial:08X}") or {}
        return (m["x"], m["y"]) if m.get("x") is not None else fallback

    # ---- bank ----
    def open_bank(self) -> int:
        mark = len(self.link.events)
        self.human.wait("speak")
        resp = self.link.send(actions.say_unicode("bank"))
        if resp != "OK":
            raise Abort(f"speech refused: {resp}")
        log('said "bank"')
        st = self.link.wait(lambda s: self._bank_opened(s, mark) is not None, timeout=5.0)
        if st is None:
            raise Abort("bank box did not open within 5 s")
        return self._bank_opened(st, mark)

    def _bank_opened(self, st, since_idx: int):
        self_serial = st["movement"]["self_serial"]
        items = st["world"]["items"]
        for ev in self.link.events[since_idx:]:
            if ev.get("ev") != "container_open":
                continue
            serial = serial_of(ev["serial"])
            it = items.get(f"0x{serial:08X}") or {}
            parent = it.get("container")
            if it.get("layer") == BANKBOX_LAYER and parent is not None and serial_of(parent) == self_serial:
                return serial
        return None

    # ---- the errand ----
    def run(self):
        st = self.link.wait(lambda s: s["movement"]["pos"] is not None
                            and s["movement"]["self_serial"] is not None, timeout=5.0)
        if st is None:
            raise Abort("proxy has no player position yet (log in first)")
        self.check_guards(st)
        self.human.wait("between")
        if self.args.start:
            target = self.args.start
            self.mover.walk_to(lambda: target, 0, "to start")
        home = tuple(self.pos()[:2])
        log(f"errand start at {home}")
        serial, label, bpos = self.find_banker()
        log(f"banker: {label} at {bpos}")
        self.mover.walk_to(lambda: self.banker_pos(serial, bpos), self.args.range, "to bank")
        box = self.open_bank()
        log(f"bank box opened (0x{box:08X})")
        self.human.wait("between")
        self.mover.walk_to(lambda: home, 0, "home")
        final = tuple(self.pos()[:2])
        if final != home:
            raise Abort(f"ended at {final}, not {home}")
        log(f"errand complete: back at {home}; {self.mover.steps} moves, {self.mover.blocked_count} blocked")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", type=lambda s: tuple(int(v) for v in s.split(",")),
                    help="walk to this x,y first; the errand returns here")
    ap.add_argument("--range", type=int, default=8, help="speak within this many tiles of the banker")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--max-blocked", type=int, default=12)
    ap.add_argument("--search-radius", type=int, default=18)
    ap.add_argument("--max-clicks", type=int, default=12)
    ap.add_argument("--human", choices=sorted(PROFILES), default="normal",
                    help="human-texture profile (humanize.py); 'off' for deterministic tests")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--no-map", action="store_true",
                    help="plan on walk memory only (offline tests against simulated worlds)")
    ap.add_argument("--control-port", type=int, default=25941)
    ap.add_argument("--state-port", type=int, default=25942)
    ap.add_argument("--memory", default=MEMORY_PATH)
    args = ap.parse_args()

    memory = nav.WalkMemory.load(args.memory)
    link = Link(args.control_port, args.state_port)
    errand = Errand(link, memory, args)
    code = 0
    try:
        errand.run()
    except Abort as e:
        log(f"ABORTED: {e}")
        code = 1
    finally:
        memory.save(args.memory)
        log(f"walk memory saved ({len(memory.tiles)} tiles, {len(memory.blocked)} blocked moves)")
    sys.exit(code)


if __name__ == "__main__":
    main()

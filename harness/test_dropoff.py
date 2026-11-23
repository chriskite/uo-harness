"""Offline proof of harness/dropoff.py against a fake home that answers as the live rental room did
(capture 20261015_210709, the user's 'New Drop Off' runs 21:29:49-22:45:44).

Run: python harness/test_dropoff.py"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions  # noqa: E402
import combat  # noqa: E402
import dropoff  # noqa: E402
import home  # noqa: E402

FAILURES = []
ME, PACK, BAG, CHEST = 0x104A60, 0x445E9F92, 0x46E9ABA2, 0x4AE0DD2C


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAILURES.append(name)


class NoHuman:
    def wait(self, kind):
        pass


class Room:
    """The room's items and menus; send() answers like the server, poll() hands the events over."""

    def __init__(self, plan, extra_items, names=None):
        self.sent, self.pending = [], []
        self.names = names or {}      # serial -> the name the server's sub 0x15 gives on an item query (0xFF sub 9)
        self.contents = {}            # container serial -> items the server sends when it opens (0x3C)
        self.menus = {int(s["serial"], 16): s["entry"] for s in plan["steps"]}
        self.target_step = next(int(s["serial"], 16) for s in plan["steps"] if s.get("target"))
        items = {f"0x{PACK:08X}": {"graphic": 0x0E75, "layer": 0x15, "container": f"0x{ME:08X}"},
                 f"0x{BAG:08X}": {"graphic": 0x0E76, "container": f"0x{PACK:08X}", "name": "bag"},
                 f"0x{CHEST:08X}": {"graphic": 0x0E40, "x": 404, "y": 922, "z": 2}}
        for s, (x, y) in zip(self.menus, [(404, 923), (403, 924), (404, 924), (404, 925), (403, 921),
                                         (401, 922), (401, 924), (402, 921)]):
            items[f"0x{s:08X}"] = {"graphic": 0x1234, "x": x, "y": y, "z": 2}
        items.update(extra_items)
        self.state = {"world": {"items": items, "containers": [], "self": {"map": 3}},
                      "movement": {"pos": [403, 923, 1], "self_serial": ME}}

    def say(self, text):
        self.pending.append({"ev": "speech_heard", "serial": 0xFFFFFFFF, "text": text})

    def send(self, pkt):
        self.sent.append(pkt)
        if pkt[0] == 0xBF and pkt[4] == 0x13:
            s = int.from_bytes(pkt[5:9], "big")
            # the unidentified-items container offers three entries, its pick is the second (index 1)
            entries = ([{"cliloc": 1095566, "index": 0, "flags": 0}, {"cliloc": 1095568, "index": 1, "flags": 0},
                        {"cliloc": 1095558, "index": 2, "flags": 0}] if self.menus[s] == 1095568 else
                       [{"cliloc": 3006349, "index": 0, "flags": 0}, {"cliloc": 3006348, "index": 1, "flags": 0}]
                       if self.menus[s] == 3006348 else [{"cliloc": self.menus[s], "index": 0, "flags": 0}])
            self.pending.append({"ev": "popup", "serial": s, "entries": entries})
        elif pkt[0] == 0xBF and pkt[4] == 0x15:
            s = int.from_bytes(pkt[5:9], "big")
            if s == self.target_step:
                self.pending.append({"ev": "target", "cursor_id": 7, "cursor_type": 0, "active": True})
            else:
                self.say("No undecoded treasure maps found in your backpack.")
        elif pkt[0] == 0x6C:
            self.say("You add 3 items to the Resource Stockpile.")
        elif pkt[0] == 0x06:
            s = int.from_bytes(pkt[1:5], "big")
            self.state["world"]["containers"].append(f"0x{s:08X}")
            self.state["world"]["items"].update(self.contents.pop(s, {}))
            self.pending.append({"ev": "container_open", "serial": s})
        elif pkt[0] == 0x08:
            s = int.from_bytes(pkt[1:5], "big")
            self.state["world"]["items"][f"0x{s:08X}"]["container"] = f"0x{CHEST:08X}"
        elif pkt[0] == 0xFF and int.from_bytes(pkt[3:7], "big") == 9:
            n = int.from_bytes(pkt[8:10], "big")
            asked = [int.from_bytes(pkt[10 + 4 * i:14 + 4 * i], "big") for i in range(n)]
            entries = [{"serial": s, "name": self.names[s]} for s in asked if s in self.names]
            for e in entries:
                self.state["world"]["items"][f"0x{e['serial']:08X}"]["name"] = e["name"]
            if entries:
                self.pending.append({"ev": "names", "count": len(entries), "entries": entries})

    def poll(self):
        evs, self.pending = self.pending, []
        return self.state, evs


class SavingRoom(Room):
    """A room whose first double-click lands in a world save: "The world is saving, please wait." at once, the
    server's answer only after "World save complete" `save_s` later (NOTES: a save holds the server 2-4 s and it
    answers what came in meanwhile afterwards)."""

    def __init__(self, plan, extra_items, save_s):
        super().__init__(plan, extra_items)
        self.save_s, self.held, self.save_end = save_s, None, None

    def send(self, pkt):
        if pkt[0] == 0x06 and self.save_end is None:
            self.say("The world is saving, please wait.")
            self.save_end = time.monotonic() + self.save_s
            self.sent.append(pkt)
            self.held = pkt
            return
        super().send(pkt)

    def poll(self):
        if self.held is not None and time.monotonic() >= self.save_end:
            self.say("World save complete. The entire process took 2.3 seconds.")
            pkt, self.held = self.held, None
            super().send(pkt)     # the server's answer now; the click itself went out (and was counted) at send
            self.sent.pop()
        return super().poll()


def test_dropoff():
    print("== the user's 'New Drop Off', then research materials, resource maps, paragon chests, runes, cloth and dyes "
          "into the chest ==")
    plan = home.for_character("Logan Wolf")["dropoff"]
    loot = {"0x44100001": {"graphic": 0x5F72, "amount": 2, "container": f"0x{PACK:08X}", "name": "research materials"},
            "0x44100002": {"graphic": 0x14EC, "amount": 1, "container": f"0x{PACK:08X}",
                           "name": "a dullwood lumber resource map"},
            "0x44100003": {"graphic": 0x5F72, "amount": 1, "container": f"0x{BAG:08X}", "name": "research materials"},
            "0x44100004": {"graphic": 0x0F0C, "amount": 20, "container": f"0x{PACK:08X}", "name": "Greater Heal potion"},
            # user 2026-11-17: "drop off paragon chests at home, not keep carrying them back to the dungeon"
            "0x44100005": {"graphic": 0x0E7C, "amount": 1, "container": f"0x{PACK:08X}",
                           "name": "paragon chest (lowtide scavenger)"},
            # user 2026-11-18: ore maps, runes, cloth and dyes too (live they stayed in the pack: "into the chest:
            # nothing"); an unnamed ore map, a Mausoleum rune, cut cloth, a mastery chain dye
            "0x44100006": {"graphic": 0x1120A, "amount": 1, "container": f"0x{PACK:08X}"},
            "0x44100007": {"graphic": 0x9BE1, "amount": 1, "container": f"0x{PACK:08X}"},
            "0x44100008": {"graphic": 0x1766, "amount": 12, "container": f"0x{PACK:08X}"},
            "0x44100009": {"graphic": 0x0E26, "amount": 1, "container": f"0x{PACK:08X}", "name": "mastery chain dye"},
            # kit: the rune tome and a dyeing tub stay
            "0x4410000A": {"graphic": 0x22C5, "amount": 1, "container": f"0x{PACK:08X}", "name": "a rune tome"},
            "0x4410000B": {"graphic": 0x0FAB, "amount": 1, "container": f"0x{PACK:08X}", "name": "a dyeing tub"}}
    room = Room(plan, loot)
    out = dropoff.run(room, NoHuman(), plan, timeout=0.5)
    picks = [(int.from_bytes(p[5:9], "big"), int.from_bytes(p[9:11], "big")) for p in room.sent
             if p[0] == 0xBF and p[4] == 0x15]
    want = [(int(s["serial"], 16), 1 if s["entry"] in (1095568, 3006348) else 0) for s in plan["steps"]]
    check("every step's entry picked by its cliloc, in the script's order (the recycler and the shelf: index 1)",
          out["ok"] and picks == want, f"{out.get('error')} {picks}")
    check("each step keeps its own line, heard before the next step's menu (no wait for it in between)",
          [len(s["lines"]) for s in out["steps"]] == [1] * len(plan["steps"])
          and out["steps"][4]["lines"] == ["You add 3 items to the Resource Stockpile."],
          str([s["lines"] for s in out["steps"]]))
    tgt = next((p for p in room.sent if p[0] == 0x6C), None)
    check("the stockpile's Restock cursor answered with the backpack, as the script's `target 0x445E9F92`",
          tgt == actions.target_object(7, PACK, 0, 0, 0, 0x0E75, 0), str(tgt))
    moved = sorted(c["serial"] for c in out["chest"])
    check("research materials, the resource map, the paragon chest, the ore map, the rune, the cloth and the dye "
          "from the pack's top level into the chest; the one in a bag, the potions, the rune tome and the tub stay",
          moved == ["0x44100001", "0x44100002", "0x44100005", "0x44100006", "0x44100007", "0x44100008",
                    "0x44100009"] and not out["left"], str(out["chest"]))
    drops = [p for p in room.sent if p[0] == 0x08]
    check("each one lifted and dropped into the chest, opened first",
          room.sent.index(actions.dclick(CHEST)) < room.sent.index(drops[0])
          and drops == [actions.drop(int(s, 16), combat.DROP_AUTO, combat.DROP_AUTO, 0, 0, CHEST) for s in moved],
          f"{len(drops)} drops")
    far = Room(plan, {})
    far.state["movement"]["pos"] = [400, 927, 1]
    try:
        dropoff.run(far, NoHuman(), plan, timeout=0.5)
        check("off the landing tile, out of reach: refused", False)
    except dropoff.DropoffError as e:
        check("off the landing tile, out of reach: refused before anything is sent",
              far.sent == [] and "within 2 tiles" in str(e), str(e))

    print("== loot the server never named (live 2026-11-22: a sealed paragon chest and a mount gear dye rode through four "
          "drop-offs): their names asked for first, as the client's hover would; kit named too stays ==")
    sealed, dye, shelf, mute = 0x4AF589DF, 0x4B1BC5A3, 0x41914174, 0x4C6E0214
    nameless = {f"0x{s:08X}": {"graphic": g, "amount": 1, "container": f"0x{PACK:08X}"}
                for s, g in ((sealed, 0x0E7C), (dye, 0x3BC0), (shelf, 0x6B92), (mute, 0x5EB6))}
    # capture 20261122_181713 22:44:07 / 22:45:17: the 0xFF sub 0x15 answers; the wall shelf is Logan's blessed kit
    room = Room(plan, nameless, names={sealed: "(sealed)", dye: "mount gear dye", shelf: "wall shelf"})
    out = dropoff.run(room, NoHuman(), plan, timeout=0.5)
    queries = [p for p in room.sent if p[0] == 0xFF and int.from_bytes(p[3:7], "big") == 9]
    check("one name query for the four, before the chest opens; the chest and the dye moved, the shelf and the item "
          "the server didn't name stay",
          out["ok"] and queries == [actions.item_query(sealed, dye, shelf, mute)]
          and room.sent.index(queries[0]) < room.sent.index(actions.dclick(CHEST))
          and sorted(c["serial"] for c in out["chest"]) == [f"0x{sealed:08X}", f"0x{dye:08X}"],
          f"{out.get('error')} {out['chest']} queries {[q.hex() for q in queries]}")
    bare = Room(plan, {})
    dropoff.run(bare, NoHuman(), plan, timeout=0.5)
    check("nothing nameless in the pack: no query",
          not any(p[0] == 0xFF and int.from_bytes(p[3:7], "big") == 9 for p in bare.sent))

    print("== the tomes in a wooden shelf by the landing (the user's house, 2026-10-21) ==")
    shelf_serial, tomes = 0x5656B661, [int(s["serial"], 16) for s in plan["steps"][:4]]
    inside = {f"0x{shelf_serial:08X}": {"graphic": 0x0A9D, "x": 401, "y": 923, "z": 1, "name": "wooden shelf"},
              **{f"0x{t:08X}": {"graphic": 0x1234, "x": 60, "y": 80, "container": f"0x{shelf_serial:08X}"}
                 for t in tomes}}       # container-local x/y: nowhere near the landing as tiles
    room = Room(plan, inside)
    out = dropoff.run(room, NoHuman(), plan, timeout=0.5)
    first_menu = next(i for i, p in enumerate(room.sent) if p[0] == 0xBF and p[4] == 0x13)
    check("tomes inside a shelf 2 tiles off: reach is the shelf's tile, the shelf opened first, every step picked",
          out["ok"] and room.sent.index(actions.dclick(shelf_serial)) < first_menu
          and len([p for p in room.sent if p[0] == 0xBF and p[4] == 0x15]) == len(plan["steps"]),
          f"{out.get('error')}")
    room = Room(plan, {**inside, f"0x{shelf_serial:08X}": {**inside[f"0x{shelf_serial:08X}"], "x": 399}})
    try:
        dropoff.run(room, NoHuman(), plan, timeout=0.5)
        check("a shelf 4 tiles off: refused", False)
    except dropoff.DropoffError as e:
        check("a shelf 4 tiles off: refused before anything is sent", room.sent == [] and "within 2 tiles" in str(e),
              str(e))

    print("== back from a trip: the client has forgotten the shelf's contents (live 2026-10-21 22:32:36) ==")
    room = Room(plan, {f"0x{shelf_serial:08X}": inside[f"0x{shelf_serial:08X}"]})
    for t in tomes:
        del room.state["world"]["items"][f"0x{t:08X}"]
    room.contents[shelf_serial] = {k: v for k, v in inside.items() if k != f"0x{shelf_serial:08X}"}
    out = dropoff.run(room, NoHuman(), plan, timeout=0.5)
    first_menu = next((i for i, p in enumerate(room.sent) if p[0] == 0xBF and p[4] == 0x13), None)
    check("tomes unknown: the shelf their steps name opened first, then every step picked",
          out["ok"] and first_menu is not None and room.sent.index(actions.dclick(shelf_serial)) < first_menu
          and len([p for p in room.sent if p[0] == 0xBF and p[4] == 0x15]) == len(plan["steps"]),
          f"{out.get('error')}")
    room = Room(plan, {f"0x{shelf_serial:08X}": inside[f"0x{shelf_serial:08X}"]})
    for t in tomes:
        del room.state["world"]["items"][f"0x{t:08X}"]
    try:
        dropoff.run(room, NoHuman(), plan, timeout=0.3)
        check("a tome not in the opened shelf: refused", False)
    except dropoff.DropoffError as e:
        check("a tome not in the opened shelf: refused before any menu",
              not any(p[0] == 0xBF for p in room.sent) and "isn't in the container 0x5656B661" in str(e), str(e))

    print("== a world save as the shelf opens (live 2026-10-29 16:57:47, hunt-20261029-160655-f26a) ==")
    room = SavingRoom(plan, {f"0x{shelf_serial:08X}": inside[f"0x{shelf_serial:08X}"]}, save_s=0.8)
    for t in tomes:
        del room.state["world"]["items"][f"0x{t:08X}"]
    room.contents[shelf_serial] = {k: v for k, v in inside.items() if k != f"0x{shelf_serial:08X}"}
    try:
        out = dropoff.run(room, NoHuman(), plan, timeout=0.3)
    except dropoff.DropoffError as e:
        out = {"ok": False, "error": str(e)}
    check("the save outlasts the 0.3 s wait: the shelf's open that comes after 'World save complete' is taken, "
          "one click, every step picked",
          out["ok"] and room.sent.count(actions.dclick(shelf_serial)) == 1
          and len([p for p in room.sent if p[0] == 0xBF and p[4] == 0x15]) == len(plan["steps"]), out.get("error"))


def main():
    test_dropoff()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILED: {json.dumps(FAILURES)}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

"""Offline proof of harness/dropoff.py against a fake home that answers as the live rental room did
(capture 20261015_210709, the user's 'New Drop Off' runs 21:29:49-22:45:44).

Run: python harness/test_dropoff.py"""
import json
import os
import sys

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

    def __init__(self, plan, extra_items):
        self.sent, self.pending = [], []
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
            self.state["world"]["containers"].append(f"0x{int.from_bytes(pkt[1:5], 'big'):08X}")
            self.pending.append({"ev": "container_open", "serial": int.from_bytes(pkt[1:5], "big")})
        elif pkt[0] == 0x08:
            s = int.from_bytes(pkt[1:5], "big")
            self.state["world"]["items"][f"0x{s:08X}"]["container"] = f"0x{CHEST:08X}"

    def poll(self):
        evs, self.pending = self.pending, []
        return self.state, evs


def test_dropoff():
    print("== the user's 'New Drop Off', then research materials and resource maps into the chest ==")
    plan = home.for_character("Logan Wolf")["dropoff"]
    loot = {"0x44100001": {"graphic": 0x5F72, "amount": 2, "container": f"0x{PACK:08X}", "name": "research materials"},
            "0x44100002": {"graphic": 0x14EC, "amount": 1, "container": f"0x{PACK:08X}",
                           "name": "a dullwood lumber resource map"},
            "0x44100003": {"graphic": 0x5F72, "amount": 1, "container": f"0x{BAG:08X}", "name": "research materials"},
            "0x44100004": {"graphic": 0x0F0C, "amount": 20, "container": f"0x{PACK:08X}", "name": "Greater Heal potion"}}
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
    check("research materials and the resource map from the pack's top level into the chest; "
          "the one in a bag and the potions stay", moved == ["0x44100001", "0x44100002"] and not out["left"],
          str(out["chest"]))
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


def main():
    test_dropoff()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILED: {json.dumps(FAILURES)}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

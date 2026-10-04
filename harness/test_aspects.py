"""Tests for harness/aspects.py: the Aspect Mastery gump exactly as the server sent
it (harness/testdata/aspect_gumps.json, Outland Dan 2026-10-04) and the server's
lines around an activation. No network.

Run: python harness/test_aspects.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import aspects  # noqa: E402

FAILURES = []
with open(os.path.join(HERE, "testdata", "aspect_gumps.json"), encoding="utf-8") as f:
    G = json.load(f)


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f" {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def parse(key):
    return aspects.parse(G[key]["layout"], G[key]["lines"])


def test_gump():
    print("== the Aspect Mastery gump ==")
    m = parse("before")
    arm = m["sections"].get("armor") or {}
    check("Arcane Essence charges and the warning level from the top row",
          m["charges"] == 429 and m["warn_below"] == 50, m)
    check("three sections top to bottom; the armor one is the one whose Activate (17) the user pressed live",
          list(m["sections"]) == ["weapon", "spellbook", "armor"] and arm["activate"] == 17
          and m["sections"]["weapon"]["activate"] == 8 and m["sections"]["spellbook"]["activate"] == 13, m)
    check("armor: Harvest, Tier 1, 361 of 1000 xp, active tier 1, icon hue 2086 (the aspected armor's hue)",
          (arm["aspect"], arm["tier"], arm["xp"], arm["xp_max"], arm["active_tier"], arm["hue"])
          == ("Harvest", 1, 361, 1000, 1, 2086), arm)
    check("its aspect arrows: left 14, right 15 (the active-tier arrow 24 isn't one)",
          (arm["prev"], arm["next"]) == (14, 15), arm)
    check("after activating, 5 charges fewer", parse("after")["charges"] == 424)


def test_lines():
    print("== the server's lines ==")
    check("activation line", aspects.activated_text("Harvest aspect armor activated."))
    check("not the follow-up bonus lines or the confirm prompt",
          not any(aspects.activated_text(t) for t in ("Harvest skill and yield bonuses become available in 1 Minute.",
                                                      "Harvest skill and yield bonuses are available.",
                                                      aspects.CONFIRM_TEXT)))
    check("already of that aspect (nothing spent)", aspects.already_text("Your armor is already of that aspect.")
          and not aspects.already_text("Harvest aspect armor activated."))


class Human:
    def wait(self, kind):
        pass


ME = 0x0014683F


def armor(hues: dict) -> dict:
    """Dan's six pieces worn (live serials), with the given hue per layer name (None: not worn)."""
    layers = {"chest": (0x46143D1A, 0x0D), "legs": (0x46143D1B, 0x04), "arms": (0x46143D1C, 0x13),
              "helmet": (0x46143D1E, 0x06), "gloves": (0x46143D1F, 0x07), "gorget": (0x46143D20, 0x0A)}
    return {f"0x{s:08X}": {"layer": layer, "container": f"0x{ME:08X}", "hue": hues.get(name, 2086)}
            for name, (s, layer) in layers.items() if hues.get(name, 2086) is not None}


class Server:
    """The server side of the menu, replaying the live gump: "[aspect" opens it, the
    armor right arrow (15) steps the armor aspect on (`armor_aspects`; the live layout
    with the armor title pointed at its own line), Activate (17) asks to confirm and
    then activates (or says the armor already has it), 0 closes."""

    def __init__(self, armor_aspects=("Harvest",), already=False):
        self.aspects, self.i, self.already = list(armor_aspects), 0, already
        self.confirm, self.queue, self.sent, self.charges = False, [], [], 429

    def gump(self):
        g = G["before"]
        lines = list(g["lines"]) + [self.aspects[self.i]]
        lines[3] = str(self.charges)
        layout = g["layout"].replace("{ text 190 385 2208 9 ", f"{{ text 190 385 2208 {len(lines) - 1} ")
        return {"ev": "gump_open", "serial": 0x5400000 + len(self.sent), "gump_id": aspects.ASPECT_GUMP,
                "layout": layout, "lines": lines}

    def say(self, text):
        self.queue.append({"ev": "speech_heard", "name": "System", "text": text})

    def send(self, pkt):
        if pkt[0] == 0xAD:
            self.sent.append("[aspect")
            self.queue.append(self.gump())
            return
        button = int.from_bytes(pkt[11:15], "big")
        self.sent.append(button)
        if button == 15:
            self.i += 1
        elif button == 17 and not self.confirm:
            self.confirm = True
            self.say(aspects.CONFIRM_TEXT)
        elif button == 17:
            self.confirm = False
            if self.already:
                self.say("Your armor is already of that aspect.")
            else:
                self.charges -= 5
                self.say(f"{self.aspects[self.i]} aspect armor activated.")
        if button:
            self.queue.append(self.gump())

    def poll(self):
        evs, self.queue = self.queue, []
        return {"movement": {"self_serial": ME}, "world": {"items": {}}}, evs


def test_flow():
    print("== reading and activating, as the user did it ==")
    s = Server()
    m = aspects.read(s, Human())
    check("read: open with [aspect, parse, close (0)", s.sent == ["[aspect", 0] and m["charges"] == 429, s.sent)
    s = Server()
    r = aspects.activate(s, "armor", Human(), "harvest")
    check("activate: Activate twice (17, 17), then close: the user's presses",
          s.sent == ["[aspect", 17, 17, 0], s.sent)
    check("activate: ok, 5 charges spent, the server's line kept",
          r["ok"] and not r["already"] and (r["charges_before"], r["charges"]) == (429, 424)
          and "Harvest aspect armor activated." in r["texts"], r)
    s = Server(already=True)
    r = aspects.activate(s, "armor", Human(), "harvest")
    check("already of that aspect: ok, nothing spent", r["ok"] and r["already"] and r["charges"] == 429, r)
    s = Server(armor_aspects=("Fire", "Frost", "Harvest"))
    r = aspects.activate(s, "armor", Human(), "harvest")
    check("another aspect shown: the right arrow (15) until Harvest, then Activate twice",
          s.sent == ["[aspect", 15, 15, 17, 17, 0] and r["ok"] and r["aspect"] == "Harvest", s.sent)
    s = Server(armor_aspects=("Fire",) * 40)
    try:
        aspects.activate(s, "armor", Human(), "harvest")
        missing = False
    except aspects.AspectError:
        missing = True
    check("an aspect never shown: refused after MAX_STEPS, nothing activated, menu closed",
          missing and 17 not in s.sent and s.sent[-1] == 0, s.sent[-3:])


def test_suit():
    print("== the worn suit, by hue ==")
    ok = aspects.suit({"items": armor({})}, ME)
    check("six pieces in the Harvest hue: a suit", ok["ok"] and len(ok["pieces"]) == 6, ok)
    s = aspects.suit({"items": armor({"chest": 2406})}, ME)
    check("the chest back from the ground in its own hue (2406): plain chest",
          not s["ok"] and s["plain"] == ["chest"], s)
    s = aspects.suit({"items": armor({"gloves": None})}, ME)
    check("no gloves worn: missing", not s["ok"] and s["missing"] == ["gloves"] and not s["plain"], s)
    s = aspects.suit({"items": armor({"chest": 1150})}, ME, hue=1150)
    check("a learned suit hue: that piece is fine, the others now aren't", s["plain"] and "chest" not in s["plain"], s)


if __name__ == "__main__":
    test_gump()
    test_lines()
    test_flow()
    test_suit()
    print("FAILED: " + ", ".join(FAILURES) if FAILURES else "ALL PASS")
    sys.exit(1 if FAILURES else 0)

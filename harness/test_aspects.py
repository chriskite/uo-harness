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


if __name__ == "__main__":
    test_gump()
    test_lines()
    print("FAILED: " + ", ".join(FAILURES) if FAILURES else "ALL PASS")
    sys.exit(1 if FAILURES else 0)

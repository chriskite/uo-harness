"""Pins harness/data/loops/lumber.json to the demonstration capture it was
written from (logs/session_20260929_204225, docs/LUMBER_LOOP.md §4).

Every loop fact the agent will act on must be visible in that capture: the
real captcha gump (id, answer entry, submit button) vs. the decoy "Captcha"
gumps, the harvest prompt and outcomes, log -> board conversion, the deed
quantum message, the rental-room menu flow, and the 60 s post-teleport harvest
lockout. A wrong id or text in lumber.json fails here instead of live.

Run: python harness/test_loop_demo.py   (offline replay, a few seconds)
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import loop_mine  # noqa: E402
import viz_feed  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP = json.load(open(os.path.join(ROOT, "harness", "data", "loops", "lumber.json"), encoding="utf-8"))
FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}{'' if cond else ' ' + detail}")
    if not cond:
        FAILURES.append(name)


def h(s):
    return int(s, 16)


def replay(tag):
    """[(t, event)] of the capture, world + proxy origin, in exact order."""
    drv = viz_feed.ReplayDriver(tag, os.path.join(ROOT, "logs"))
    out, cursor = [], 0
    while drv.position < len(drv.items):
        drv._advance(None, 200)
        tap = drv.tap
        for env in tap.events[max(cursor - tap.events_base, 0):]:
            out.append((env["t"], env["data"]))
        cursor = tap.events_base + len(tap.events)
    return drv, out


def texts(evs):
    """[(t, text)] of everything the client was told: plain speech + cliloc numbers."""
    out = []
    for t, e in evs:
        if e["ev"] == "speech_heard":
            out.append((t, e["text"]))
        elif e["ev"] == "cliloc":
            out.append((t, e["cliloc"]))
    return out


def after(evs, t0, pred, within=5.0):
    return next((e for t, e in evs if t0 <= t <= t0 + within and pred(e)), None)


def main():
    drv, evs = replay(LOOP["source_session"])
    heard = texts(evs)
    said = {x for _, x in heard}
    print("== capture")
    check("exact interleave", drv.order == "exact", drv.order_note or "")

    print("== captcha: the real one vs the decoys")
    cap = LOOP["captcha"]
    gumps = [(t, e) for t, e in evs if e["ev"] == "gump_open"]
    real = [(t, e) for t, e in gumps if e["gump_id"] == h(cap["gump_id"])]
    check("real captcha gump seen", len(real) >= 1)
    lay = loop_mine.parse_gump_layout(real[0][1]["layout"]) if real else {}
    check("real captcha: answer entry + submit button",
          lay.get("entries") == [cap["answer_entry_id"]] and lay.get("buttons")[-1:] == [cap["submit_button"]],
          repr(lay))
    reply = next((e for t, e in evs if e["ev"] == "gump_response" and e["gump_id"] == h(cap["gump_id"])), None)
    check("human answer used the entry and button",
          reply is not None and reply["button_id"] == cap["submit_button"]
          and [x["id"] for x in reply["texts"]] == [cap["answer_entry_id"]])
    check("answer acknowledged", cap["ok_text"] in said)
    decoys = [e for _, e in gumps if e["lines"][:1] == ["Captcha"] and e["gump_id"] != h(cap["gump_id"])]
    check("decoy Captcha gumps opened on harvest attempts", len(decoys) >= 10, str(len(decoys)))
    check("decoys have no reply buttons",
          all(loop_mine.parse_gump_layout(e["layout"])["buttons"] == [] for e in decoys))
    check("decoys draw their text offscreen",
          all(re.search(r"croppedtext -\d+ -\d+", e["layout"]) for e in decoys))
    check("decoy gump ids are all different", len({e["gump_id"] for e in decoys}) == len(decoys))
    decoy_ids = {e["gump_id"] for e in decoys}
    check("nobody ever replied to a decoy",
          not any(e["ev"] == "gump_response" and e["gump_id"] in decoy_ids for _, e in evs))

    print("== harvest")
    hv, items = LOOP["harvest"], LOOP["items"]
    hatchet = h(items["hatchet"]["serial"])
    dclicks = [t for t, e in evs if e["ev"] == "dclick" and e["serial"] == hatchet]
    prompted = [t for t in dclicks if after(evs, t, lambda e: e["ev"] == "cliloc" and e["cliloc"] == hv["prompt_cliloc"], 1.0)]
    check("hatchet dclick -> 'What do you want to use this item on?'", len(prompted) >= 10, f"{len(prompted)}/{len(dclicks)}")
    cursors = [e for t, e in evs if e["ev"] == "target"]
    check("tool cursor is a location cursor", any(e["target_type"] == hv["cursor_target_type"] for e in cursors))
    tree = hv["trees"][0]
    check("tree target (x, y, z, graphic) seen",
          any(e["ev"] == "target_response" and (e["x"], e["y"], e["z"], e["graphic"]) ==
              (tree["x"], tree["y"], tree["z"], h(tree["graphic"])) for _, e in evs))
    check("fail cliloc seen", hv["fail_cliloc"] in said)
    check("success text seen", hv["success_text"] in said)

    print("== convert, deed, vendor")
    check("conversion text seen", LOOP["convert"]["ok_text"] in said)
    check("deed prompt + quantum message", LOOP["deed"]["prompt_text"] in said and LOOP["deed"]["too_few_text"] in said)
    check("quantum in the message", str(LOOP["deed"]["quantum"]) in LOOP["deed"]["too_few_text"])
    menu = next((e for _, e in evs if e["ev"] == "popup" and e["serial"] == h(LOOP["npcs"]["banker"]["serial"])), None)
    buy_idx = LOOP["vendor"]["banker_context_menu"]["buy_index"]
    check("banker menu index -> Buy (3006103)",
          menu is not None and any(x["index"] == buy_idx and x["cliloc"] == 3006103 for x in menu["entries"]))
    blist = next((e for _, e in evs if e["ev"] == "buy_list"), None)
    check("blank commodity price",
          blist is not None and {"price": LOOP["vendor"]["blank_commodity_price"], "name": "Blank Commodity"} in blist["items"])
    check("purchase acknowledged", LOOP["vendor"]["buy_ok_text"] in said)

    print("== rental room")
    room = LOOP["room"]
    menu_id = h(room["menu_gump_id"])
    enters = [t for t, e in evs if e["ev"] == "gump_response" and e["gump_id"] == menu_id
              and e["button_id"] == room["enter_button"]]
    check("room menu button 4 -> entered",
          any(after(evs, t, lambda e: e["ev"] == "speech_heard" and e["text"] == room["enter_text"], 1.0) for t in enters))
    check("rent confirmation", room["rent_ok_text"] in said)
    door_click = next((t for t, e in evs if e["ev"] == "dclick" and e["serial"] == h(room["door"]["serial"])), None)
    check("door dclick opens the room menu",
          door_click is not None and after(evs, door_click, lambda e: e["ev"] == "gump_open" and e["gump_id"] == menu_id, 1.0) is not None)
    check("exit text seen", room["exit_text"] in said)
    sec = h(room["secure_container"]["serial"])
    drops = [e for _, e in evs if e["ev"] == "drop" and e["container"] == sec]
    check("boards + deed dropped into the secure container at the auto-position",
          len(drops) >= 2 and all(e["x"] == e["y"] == room["drop_into_container_xy"] for e in drops))

    print("== travel lockout")
    lock = LOOP["travel_lockout"]
    t_exit = next(t for t, x in heard if x == room["exit_text"])
    hit = next(((t, x) for t, x in heard if isinstance(x, str) and x.startswith(lock["text_prefix"])), None)
    check("lockout message seen", hit is not None)
    if hit:
        wait = int(re.search(r"wait (\d+) seconds", hit[1]).group(1))
        total = (hit[0] - t_exit) + wait
        check("elapsed since room exit + remaining wait == 60 s", abs(total - lock["seconds"]) <= 2.0, f"{total:.1f}")

    print(f"\nloop demo: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

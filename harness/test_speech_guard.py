"""Tests for harness/speech_guard.py: who counts as a character speaking near
us during a harvest job. Cases are shapes seen in the captures (2026-10-01
survey): player speech ("bank" from Kanbalt), Outlands' click echoes (title and
guild tag 0.04-0.06 s after a 0x09), vendor lines, pets' "(bonded)", damage
numbers, item messages.

Run: python harness/test_speech_guard.py   (pure, < 1 s)
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import alerts  # noqa: E402
from memory import Memory  # noqa: E402
from speech_guard import CLEAR_S, SpeechGuard, pet_command, speaker, staff_hints  # noqa: E402

FAILURES = []
ME, PLAYER, VENDOR, PET, MOB, STAFF = 0x00094375, 0x0000ABCD, 0x00000B8D, 0x0000C001, 0x0000C002, 0x0000D00D


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {'' if cond else detail}")
    if not cond:
        FAILURES.append(name)


def world():
    return {"self": {"serial": f"0x{ME:08X}"},
            "mobiles": {
                f"0x{PLAYER:08X}": {"graphic": 0x190, "notoriety": 1, "flags": 0x20, "x": 5, "y": 5},
                f"0x{VENDOR:08X}": {"graphic": 0x191, "notoriety": 7, "flags": 0x02},
                f"0x{PET:08X}": {"graphic": 0x2CB, "notoriety": 1, "flags": 0},
                f"0x{MOB:08X}": {"graphic": 0x27, "notoriety": 4, "flags": 0x40},
                f"0x{STAFF:08X}": {"graphic": 0x3DB, "notoriety": 7, "flags": 0x20},
            },
            "labels": {f"0x{VENDOR:08X}": "Jamie the provisioner", f"0x{PLAYER:08X}": "Kanbalt"}}


def said(serial, text, type_=0, name="x"):
    return {"ev": "speech_heard", "serial": serial, "name": name, "type": type_, "hue": 0x3B2, "text": text}


def test_speaker():
    print("== speaker: a character speaking, or not ==")
    w = world()
    who = speaker(w, said(PLAYER, "bank", name="Kanbalt"))
    check("a player saying something counts (Kanbalt 'bank')", who is not None and who["serial"] == "0x0000ABCD"
          and who["type"] == "say" and who["on_screen"] and "player flag 0x20" in who["evidence"], str(who))
    check("emote / whisper / yell count", all(speaker(w, said(PLAYER, "hey", t)) for t in (2, 8, 9)))
    for ev, why in ((said(VENDOR, "The total of thy purchase is 10 gold."), "a vendor (notoriety 7, no player flag)"),
                    (said(PET, "(bonded)"), "a pet (non-human body)"),
                    (said(MOB, "-57"), "a damage number"),
                    (said(PLAYER, "12"), "a bare number"),
                    (said(PLAYER, "Kanbalt", 6), "a click label (type 6)"),
                    (said(PLAYER, "In Vas Mani", 10), "spell words (type 10)"),
                    (said(PLAYER, "guild chat", 13), "guild chat"),
                    (said(0xFFFFFFFF, "Welcome TestWorth!"), "the server"),
                    (said(0x45CE64A1, "Emptying"), "an item"),
                    (said(ME, "room"), "our own speech"),
                    (said(PLAYER, "   "), "blank text")):
        check(f"ignored: {why}", speaker(w, ev) is None, str(speaker(w, ev)))
    w["mobiles"][f"0x{PLAYER:08X}"]["flags"] = 0
    w["labels"][f"0x{PLAYER:08X}"] = "Raynor the herbalist"
    check("ignored: a human with a 'Name the <title>' label and no player flag (an NPC)",
          speaker(w, said(PLAYER, "Hail adventurer")) is None)
    w["labels"].pop(f"0x{PLAYER:08X}")
    check("a human body without npc evidence counts (the conservative reading)",
          speaker(w, said(PLAYER, "hello")) is not None)
    staff = speaker(w, said(STAFF, "Hello there", name="GM Kemp"))
    check("player flag at notoriety 7 still counts (staff may be invulnerable), with staff hints",
          staff is not None and "GM body 0x03DB" in staff["evidence"] and "staff-like name" in staff["evidence"],
          str(staff))
    hidden = speaker(w, said(0x00001234, "What are you doing?", name="Seer Ann"))
    check("a speaker the client doesn't have (hidden or out of view) counts",
          hidden is not None and not hidden["on_screen"] and "staff-like name" in hidden["evidence"], str(hidden))


def test_scan():
    print("== SpeechGuard.scan: history, click echoes, all-clear ==")
    now = [1000.0]
    g = SpeechGuard(now=lambda: now[0])
    w = world()
    events, times = [said(PLAYER, "said before the job started")], [1.0]
    check("the first scan only marks history", g.scan(w, events, times) == [])
    events += [{"ev": "query", "serial": PLAYER, "kind": 0x09}, said(PLAYER, "Viceroy"), said(PLAYER, "[Veteran, J4F]")]
    times += [10.0, 10.05, 10.05]
    check("Outlands' click echo (title, guild tag 0.05 s after a 0x09) is not speech",
          g.scan(w, events, times) == [], str(g.upto))
    events.append(said(PLAYER, "bank"))
    times.append(29.2)
    got = g.scan(w, events, times)
    check("the same player speaking 19 s later counts", [x["text"] for x in got] == ["bank"], str(got))
    check("a scan only reports new events", g.scan(w, events, times) == [])
    g.clear(["0x0000ABCD"])
    events.append(said(PLAYER, "anyone selling logs?"))
    times.append(40.0)
    check("an all-clear covers that speaker", g.scan(w, events, times) == [])
    now[0] += CLEAR_S + 1
    events.append(said(PLAYER, "hello?"))
    times.append(50.0)
    check("until it runs out", [x["text"] for x in g.scan(w, events, times)] == ["hello?"])


def test_context():
    print("== SpeechGuard.scan: each line carries the recent speech around it (triage.py) ==")
    now = [1000.0]
    g = SpeechGuard(now=lambda: now[0])
    w = world()
    w["self"]["name"] = "TestWorth"
    events, times = [], []
    g.scan(w, events, times)
    events += [said(PLAYER, "old news"), said(STAFF, "anyone selling logs?", name="GM Kemp"),
               said(ME, "no sorry"), {"ev": "query", "serial": PLAYER, "kind": 0x09}, said(PLAYER, "Viceroy"),
               said(VENDOR, "The total of thy purchase is 10 gold.")]
    times += [1.0, 200.0, 201.0, 202.0, 202.05, 203.0]
    g.scan(w, events, times)
    g.clear(["0x0000D00D"])
    events += [said(STAFF, "hm"), said(PLAYER, "what are you doing?")]
    times += [204.0, 205.0]
    got = g.scan(w, events, times)
    ctx = [(c["name"], c["text"]) for c in got[0]["context"]] if got else []
    check("context: lines within RECENT_S, ours and a cleared speaker's included, no click echo or vendor, "
          "ending with the line itself",
          [x["text"] for x in got] == ["what are you doing?"]
          and ctx == [("GM Kemp", "anyone selling logs?"), ("TestWorth", "no sorry"), ("x", "hm"),
                      ("Kanbalt", "what are you doing?")], str(ctx))


def test_staff():
    print("== staff hints and the repeating staff alarm ==")
    w = world()
    check("no staff hints for an ordinary player", staff_hints(speaker(w, said(PLAYER, "hi"))) == [])
    check("GM body + staff-like name are hints",
          staff_hints(speaker(w, said(STAFF, "Hello there", name="GM Kemp"))) == ["GM body 0x03DB", "staff-like name"])
    check("a speaker not on screen is a hint (hidden staff speak without a body)",
          staff_hints(speaker(w, said(0x00001234, "hm"))) == ["not on screen (hidden or out of view)"])
    os.environ[alerts.QUIET_ENV] = "1"
    m = Memory(os.path.join(tempfile.mkdtemp(), "harness.db"))
    check("nothing open: no alarm", alerts.staff_alarm_due(m, now=1000.0) is False)
    jid = alerts.post_gm(m, "lumber", "GM Kemp: GM body", {})
    t0 = float(alerts.tw.meta_get(m, alerts.LAST_STAFF_KEY))
    check("posting sounds it at once (no repeat right after)", alerts.staff_alarm_due(m, now=t0 + 1) is False)
    check("repeats after STAFF_REPEAT_S", alerts.staff_alarm_due(m, now=t0 + alerts.STAFF_REPEAT_S) is True)
    check("and not again within the next interval",
          alerts.staff_alarm_due(m, now=t0 + alerts.STAFF_REPEAT_S + 5) is False)
    m.juncture_ack(jid)
    check("acked: silent", alerts.staff_alarm_due(m, now=t0 + 10 * alerts.STAFF_REPEAT_S) is False)
    m.close()


def test_pet_commands():
    print("== tamer pet commands are not a character speaking to us (NPD, 2026-10-02) ==")
    w = world()
    w["self"]["name"] = "Hackworth"
    w["mobiles"][f"0x{PET:08X}"]["name"] = "Fluffy"
    w["mobiles"][f"0x{MOB:08X}"]["name"] = "Hackworth"     # a pet named like us: our name still wins
    live = ["all guard", "All Stop", "All Guard Me", "All Kill", "ALL KILL"]   # junctures 52/56/57/58/60
    check("the five live lines are pet commands", all(pet_command(t, w) for t in live))
    check("trailing punctuation and spaces ignored", pet_command("  all follow me!! ", w))
    check("a pet on screen by name ('Fluffy kill')", pet_command("fluffy kill", w))
    for text, why in (("Hackworth stop", "our own name in the name slot"), ("hackworth come", "our name, lower case"),
                      ("stop", "a bare command"), ("all guard me please", "an extra word after"),
                      ("can you all stop", "words before"), ("please stop", "a name slot that is no pet on screen"),
                      ("Kanbalt stop", "a human's name in the name slot"), ("all dance", "not a pet command")):
        check(f"not a pet command: {why} ({text!r})", not pet_command(text, w))
    g = SpeechGuard(now=lambda: 1000.0)
    events, times = [], []
    g.scan(w, events, times)
    events += [said(PLAYER, t, name="Kanbalt") for t in live]
    times += [10.0, 11.0, 12.0, 13.0, 14.0]
    check("scan: the live lines hold nothing", g.scan(w, events, times) == [])
    events += [said(PLAYER, t, name="Kanbalt") for t in ("Hackworth stop", "stop", "all guard me please")]
    events.append(said(STAFF, "all kill", name="GM Kemp"))
    events.append(said(0x00001234, "All Stop", name="Ann"))
    times += [20.0, 21.0, 22.0, 23.0, 24.0]
    got = g.scan(w, events, times)
    check("scan: own name, bare command, extra words, GM body and not-on-screen speakers still hold",
          [x["text"] for x in got] == ["Hackworth stop", "stop", "all guard me please", "all kill", "All Stop"]
          and staff_hints(got[3]) and staff_hints(got[4]), str([(x["text"], x["evidence"]) for x in got]))
    check("the pet commands are still in a later line's context",
          [c["text"] for c in got[0]["context"]] == live + ["Hackworth stop"], str(got[0]["context"]))


if __name__ == "__main__":
    test_speaker()
    test_scan()
    test_context()
    test_staff()
    test_pet_commands()
    print("ALL PASS" if not FAILURES else f"FAILED: {len(FAILURES)}: {FAILURES}")
    sys.exit(1 if FAILURES else 0)

"""Tests for harness/triage.py: the Laya verdict on a line heard during a
harvest job. A stand-in laya-serve (stdlib HTTP) returns fixed scores, so the
tests cover our side: the escalation threshold, the prompt we send, failure
and back-off. Laya's own accuracy is measured with harness/eval_triage.py.

Run: python harness/test_triage.py   (pure, < 1 s)
"""
import http.server
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import triage  # noqa: E402
from speech_guard import staff_hints  # noqa: E402

FAILURES = []
ME, PLAYER, VENDOR, PET, FAR, QUIET = 0x00094375, 0x0000ABCD, 0x00000B8D, 0x0000C001, 0x0000BEEF, 0x0000CAFE


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {'' if cond else detail}")
    if not cond:
        FAILURES.append(name)


class Fake:
    """laya-serve stand-in: answers /v1/systemone with `scores`, or `status`."""

    def __init__(self):
        self.scores, self.status, self.bodies = {"check": 0.1, "direct": 0.2}, 200, []
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                fake.bodies.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
                out = json.dumps({"answers": {k: {"noul": v} for k, v in fake.scores.items()},
                                  "routing": {"model": "english"}}).encode()
                self.send_response(fake.status)
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


def world():
    return {"self": {"serial": f"0x{ME:08X}", "name": "TestWorth", "x": 100, "y": 100},
            "mobiles": {
                f"0x{ME:08X}": {"graphic": 0x190, "flags": 0x20, "x": 100, "y": 100},
                f"0x{PLAYER:08X}": {"graphic": 0x190, "notoriety": 1, "flags": 0x20, "x": 103, "y": 101,
                                    "name": "Kanbalt"},
                f"0x{VENDOR:08X}": {"graphic": 0x191, "notoriety": 7, "flags": 0x02, "x": 101, "y": 100},
                f"0x{PET:08X}": {"graphic": 0x2CB, "notoriety": 1, "flags": 0, "x": 100, "y": 101},
                f"0x{FAR:08X}": {"graphic": 0x190, "notoriety": 1, "flags": 0x20, "x": 130, "y": 100, "name": "Far"},
                f"0x{QUIET:08X}": {"graphic": 0x191, "notoriety": 1, "flags": 0x20, "x": 95, "y": 100},
            },
            "labels": {f"0x{VENDOR:08X}": "Jamie the provisioner"}}


def line(text, context=None):
    return {"serial": f"0x{PLAYER:08X}", "name": "Kanbalt", "label": None, "text": text,
            "evidence": ["player flag 0x20"], "context": context or [{"name": "Kanbalt", "text": text}]}


def test_threshold(fake):
    print("== escalation: an attendance check is a staff hint ==")
    t = triage.Triage(fake.url, log=lambda m: None)
    fake.scores = {"check": triage.ESCALATE_CHECK, "direct": 0.9}
    w = line("are you there?")
    v = t.judge(w, world())
    check("check at the threshold: verdict stored, staff hint added",
          w["triage"] is v and v["check"] == triage.ESCALATE_CHECK and v["v"] == triage.VERSION
          and staff_hints(w) == [f"attendance check (laya {triage.ESCALATE_CHECK:.2f})"], str(w))
    fake.scores = {"check": triage.ESCALATE_CHECK - 0.01, "direct": 0.99}
    w = line("hi")
    t.judge(w, world())
    check("just below it (whatever `direct` says): no hint, still recorded",
          staff_hints(w) == [] and w["triage"]["direct"] == 0.99, str(w))


def test_prompt(fake):
    print("== the prompt: who we are, who's near, the speech ending with the judged line ==")
    t = triage.Triage(fake.url, log=lambda m: None)
    ctx = [{"name": "Far", "text": "anyone selling logs?"}, {"name": "TestWorth", "text": "no sorry"},
           {"name": "Kanbalt", "text": "what are you doing?"}]
    t.judge(line("what are you doing?", ctx), world(), names={f"0x{QUIET:08X}": "Vorn"})
    body = fake.bodies[-1]
    st = body["state"]
    check("asks both questions on the pinned checkpoint",
          set(body["questions"]) == {"check", "direct"} and body["model"] == triage.MODEL, str(body)[:200])
    check("near me: players in view with distance, nearest first, a nameless one by the name it spoke under; "
          "not us, the vendor, the pet or one out of view",
          "near me: Kanbalt (3 tiles away), Vorn (5 tiles away)." in st and "Jamie" not in st and "Far (" not in st, st)
    check("we're named and the judged line is last", st.startswith("I am TestWorth,")
          and st.endswith("Far: anyone selling logs?\nTestWorth: no sorry\nKanbalt: what are you doing?"), st)


def test_failure(fake):
    print("== failure: an error verdict, no hint, a back-off ==")
    now = [0.0]
    logged = []
    t = triage.Triage(fake.url, log=logged.append, now=lambda: now[0])
    fake.status, fake.scores = 500, {"check": 0.99, "direct": 0.99}
    n0 = len(fake.bodies)
    w = line("are you there?")
    v = t.judge(w, world())
    check("a 500 is an error verdict with the prompt, and no hint from it",
          "error" in v and "state" in v and staff_hints(w) == [], str(v)[:200])
    t.judge(line("hello?"), world())
    now[0] += triage.BACKOFF_S - 1
    t.judge(line("hello??"), world())
    check("within BACKOFF_S nothing is sent, and the outage is logged once",
          len(fake.bodies) == n0 + 1 and len(logged) == 1, f"{len(fake.bodies) - n0} {logged}")
    fake.status = 200
    now[0] += 1
    w = line("u there?")
    t.judge(w, world())
    check("after it, calls resume", len(fake.bodies) == n0 + 2 and "check" in w["triage"], str(w["triage"])[:200])
    w = line("hi")
    check("url empty = off: no verdict, line untouched",
          triage.Triage("").judge(w, world()) is None and "triage" not in w)


if __name__ == "__main__":
    f = Fake()
    test_threshold(f)
    test_prompt(f)
    test_failure(f)
    f.srv.shutdown()
    print("ALL PASS" if not FAILURES else f"FAILED: {len(FAILURES)}: {FAILURES}")
    sys.exit(1 if FAILURES else 0)

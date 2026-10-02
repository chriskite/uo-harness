"""Tests for harness/telegram_bridge.py against a fake Bot API on localhost:
what reaches the phone (and in what order, loud or silent), what comes back as
user chat, and delivery through rate limits and refusals. No network.

    python harness/test_telegram_bridge.py
"""
import http.server
import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import task_wrap as tw  # noqa: E402
import telegram_bridge as tb  # noqa: E402
from memory import Memory  # noqa: E402

FAILURES = []
TOKEN, CHAT, STRANGER = "123:TEST", 4242, 999


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {'' if cond else detail}")
    if not cond:
        FAILURES.append(name)


class FakeApi:
    """Bot API stand-in. `calls` records (method, params); `send_errors` is a queue of
    error bodies for the next sendMessage calls; `updates` are served by offset."""

    def __init__(self):
        self.calls, self.send_errors, self.updates = [], [], []
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                params = json.loads(self.rfile.read(int(self.headers["content-length"])) or b"{}")
                token, method = self.path.split("/")[1][3:], self.path.split("/")[2]
                fake.calls.append((method, params))
                status, body = 200, {"ok": True, "result": True}
                if token != TOKEN:
                    status, body = 401, {"ok": False, "error_code": 401, "description": "Unauthorized"}
                elif method == "sendMessage" and fake.send_errors:
                    body = fake.send_errors.pop(0)
                    status = body["error_code"]
                elif method == "getUpdates":
                    body = {"ok": True, "result": [u for u in fake.updates
                                                   if u["update_id"] >= params.get("offset", 0)]}
                elif method == "getMe":
                    body = {"ok": True, "result": {"username": "fake_bot"}}
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def sent(self):
        return [p for m, p in self.calls if m == "sendMessage"]

    def reset(self):
        self.calls.clear()


def setup(api, **kw):
    db = os.path.join(tempfile.mkdtemp(prefix="tgtest"), "harness.db")
    mem = Memory(db)
    slept = []
    return mem, tb.Bridge(mem, tb.Bot(TOKEN, api.url), CHAT, sleep=slept.append, **kw), slept


def msg(update_id, text, chat=CHAT, **extra):
    return {"update_id": update_id, "message": {"message_id": update_id * 10, "chat": {"id": chat, "type": "private"},
                                                "text": text, **extra}}


def test_outbound(api):
    print("== store -> Telegram: what is forwarded, loud or silent, in time order ==")
    mem, b, _ = setup(api)
    mem.chat_post("overseer", "old history", t=1.0)
    mem.juncture("lumber", "stuck", "old stuck", "attention", t=1.0)
    b.init_cursors()
    check("a first run sends no old history", b.pump_out() == 0 and api.sent() == [], str(api.sent()))
    cap = mem.juncture("lumber", "captcha", "Captcha up; agent paused", "urgent", t=10.0)
    mem.chat_post("overseer", "on it", t=11.0)
    mem.chat_post("overseer", "reading the captcha juncture", "thought", t=12.0)
    mem.chat_post("overseer", "act walk 2", "action", t=13.0)
    mem.chat_post("overseer", "recalled 'x'", "memory", t=14.0)
    mem.chat_post("user", "typed in the viz", t=15.0)
    phone = mem.chat_post("user", "typed on the phone", data={"via": tb.VIA}, t=16.0)
    mem.juncture("lumber", "trip_done", "trip 1 stored 30", "info", t=17.0)
    done = mem.juncture("lumber", "task_done", "lumber finished (exit 0)", "info", t=18.0)
    acked = mem.juncture("lumber", "stuck", "route blocked", "attention", t=9.0)   # out of time order
    mem.juncture_ack(acked)
    b.pump_out()
    got = [(p["text"].split("\n")[0], p["disable_notification"]) for p in api.sent()]
    want = [(f"URGENT captcha #{cap} (lumber)", False), ("overseer: on it", False),
            ("you (viz): typed in the viz", True), (f"INFO task_done #{done} (lumber)", True),
            (f"ATTENTION stuck #{acked} (lumber)", False)]
    check("overseer messages and waking junctures, loud; viz lines and a task's end, silent; "
          "no thoughts, actions, memory, phone echoes or plain info", got == want, str(got))
    check("an acked juncture says so", api.sent()[-1]["text"].endswith("(already acked)"), api.sent()[-1]["text"])
    check("every message goes to the paired chat", all(p["chat_id"] == CHAT for p in api.sent()))
    api.reset()
    check("cursors persist: nothing is sent twice", b.pump_out() == 0 and api.sent() == [])
    check("cursors are in the store", tw.meta_get(mem, tb.CHAT_CURSOR_KEY) == str(phone)
          and tw.meta_get(mem, tb.JUNCTURE_CURSOR_KEY) == str(acked))

    b2 = tb.Bridge(mem, b.bot, CHAT, thoughts=True, actions=True, min_rank=0)
    mem.chat_post("overseer", "a thought", "thought")
    mem.chat_post("overseer", "act heal", "action")
    trip = mem.juncture("lumber", "trip_done", "trip 2", "info")
    b2.pump_out()
    got = [(p["text"].split("\n")[0], p["disable_notification"]) for p in api.sent()]
    check("--thoughts --actions --min-severity info forward those, silently",
          got == [("overseer (thinking): a thought", True), ("overseer did: act heal", True),
                  (f"INFO trip_done #{trip} (lumber)", True)], str(got))
    api.reset()


def test_delivery(api):
    print("== delivery: rate limits are waited out, refusals skipped, long text split ==")
    mem, b, slept = setup(api)
    b.init_cursors()
    api.send_errors[:] = [{"ok": False, "error_code": 429, "description": "Too Many Requests",
                           "parameters": {"retry_after": 7}}]
    mem.chat_post("overseer", "after a rate limit")
    b.pump_out()
    check("429: waits retry_after, then sends once", slept == [7]
          and [p["text"] for p in api.sent()] == ["overseer: after a rate limit"] * 2, f"{slept} {api.sent()}")
    api.reset()
    api.send_errors[:] = [{"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}]
    mem.chat_post("overseer", "refused")
    mem.chat_post("overseer", "next one")
    b.pump_out()
    check("400: that message is skipped and the next still goes",
          [p["text"] for p in api.sent()] == ["overseer: refused", "overseer: next one"], str(api.sent()))
    api.reset()
    long = "\n".join(f"line {i} " + "x" * 90 for i in range(100))
    mem.chat_post("overseer", long)
    b.pump_out()
    parts = [p["text"] for p in api.sent()]
    check("a long message arrives whole, in parts of at most 4096 characters",
          len(parts) == 3 and all(len(p) <= tb.MSG_MAX for p in parts)
          and "\n".join(parts) == "overseer: " + long, str([len(p) for p in parts]))
    api.reset()


def test_inbound(api):
    print("== Telegram -> store: the paired chat's text becomes user chat ==")
    mem, b, _ = setup(api)
    api.updates[:] = [msg(100, "/start"), msg(101, "  go chop at the ridge  "),
                      msg(102, "who is this", chat=STRANGER), msg(103, "", sticker={"emoji": "x"}),
                      msg(104, "y" * (tb.CHAT_MAX + 1))]
    n = b.pump_in(timeout=0)
    rows = mem.chat(role="user")
    check("one user row, trimmed, marked via telegram", n == 1 and [(r["text"], r["data"]) for r in rows]
          == [("go chop at the ridge", {"via": "telegram", "message_id": 1010})], str(rows))
    check("the offset moves past every update", tw.meta_get(mem, tb.UPDATE_OFFSET_KEY) == "105")
    replies = [p["text"] for p in api.sent()]
    check("replies: no overseer running, text only, too long; nothing to the stranger",
          len(replies) == 3 and replies[0].startswith("No overseer running (never seen)")
          and replies[1] == "Only text messages reach the overseer." and replies[2].startswith("Too long")
          and all(p["chat_id"] == CHAT for p in api.sent()), str(replies))
    gu = [p for m, p in api.calls if m == "getUpdates"][-1]
    check("getUpdates asks from the stored offset", gu["offset"] == 0 and gu["allowed_updates"] == ["message"], str(gu))
    api.reset()
    tw.meta_set(mem, "overseer_heartbeat", f"{time.time():.2f}")
    api.updates[:] = [msg(105, "status?")]
    b.pump_in(timeout=0)
    gu = [p for m, p in api.calls if m == "getUpdates"][-1]
    check("next poll from offset 105; with an overseer running, no reply",
          gu["offset"] == 105 and api.sent() == [] and mem.chat(role="user")[-1]["text"] == "status?", str(api.sent()))
    api.reset()
    b.init_cursors()
    b.pump_out()
    check("phone lines aren't echoed back to the phone", api.sent() == [], str(api.sent()))
    api.updates[:] = []
    api.reset()


def test_pair(api):
    print("== pair: the next private message's chat is saved ==")
    cfg_path = os.path.join(tempfile.mkdtemp(prefix="tgpair"), "telegram.json")
    os.environ[tb.API_ENV] = api.url
    api.updates[:] = [msg(7, "hi", chat=555)]
    with open(cfg_path, "w") as f:
        json.dump({"token": TOKEN}, f)
    code = tb.main(["--config", cfg_path, "pair", "--timeout", "5"])
    cfg = tb.load_config(cfg_path)
    check("chat saved with the token", code == 0 and cfg == {"token": TOKEN, "chat_id": 555}, str(cfg))
    check("its update is confirmed, and the chat told",
          ("getUpdates", {"offset": 8, "timeout": 0}) in api.calls
          and [(p["chat_id"], p["text"][:7]) for p in api.sent()] == [(555, "Paired:")], str(api.calls))
    api.reset()
    with open(cfg_path, "w") as f:
        json.dump({"token": "123:WRONG", "chat_id": 555}, f)
    check("a bad token fails cleanly", tb.main(["--config", cfg_path, "send", "x"]) == 1)
    del os.environ[tb.API_ENV]
    api.updates[:] = []
    api.reset()


if __name__ == "__main__":
    fake = FakeApi()
    test_outbound(fake)
    test_delivery(fake)
    test_inbound(fake)
    test_pair(fake)
    print(f"\n{len(FAILURES)} failure(s)" + (": " + ", ".join(FAILURES) if FAILURES else ""))
    sys.exit(1 if FAILURES else 0)

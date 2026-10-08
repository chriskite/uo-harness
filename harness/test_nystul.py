"""Tests for Nystul the Wizard, the viz AI assistant (harness/nystul.py, nystul_tools.py).

  python harness/test_nystul.py

- tools: sealed and outside paths refused, numbered reads, case-insensitive grep, read-only
  SQL (writes, ATTACH and journal pragmas refused; row-limit footer), a missing Discord DB,
  the ctl and viz-route allowlists
- parse_event over a canned omp JSON stream: partial text, steps, cost, final answer
- runs against a fake omp: answer stored, transcript carried into the next prompt, a second
  ask while running 409, cancel, a failing run kept as error with its stderr, MAX_RUNS,
  a `running` row left by a dead viz marked interrupted
- HTTP: POST /api/nystul/ask, GET /api/nystul and /api/nystul/<id>, 400/404 errors
"""
import json
import os
import sqlite3
import sys
import tempfile
import textwrap
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import memory  # noqa: E402
import nystul  # noqa: E402
import nystul_tools as nt  # noqa: E402
import test_viz as tv  # noqa: E402
import viz_feed  # noqa: E402
import viz_server  # noqa: E402

check, FAILURES = tv.check, tv.FAILURES

FAKE_OMP = textwrap.dedent(r'''
    import json, sys, time
    path = next(a[1:] for a in sys.argv[1:] if a.startswith("@"))
    body = open(path, encoding="utf-8").read()
    question = body.split("## Question", 1)[1]
    if "SLOW" in question:
        time.sleep(60)
    if "FAIL" in question:
        sys.stderr.write("boom: the fake omp failed\n")
        sys.exit(1)
    def out(ev):
        print(json.dumps(ev), flush=True)
    out({"type": "session", "id": "x"})
    out({"type": "message_start", "message": {"role": "assistant"}})
    out({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "looking"}})
    out({"type": "tool_execution_start", "toolCallId": "t1", "toolName": "uo_read", "args": {"path": "README.md"}})
    out({"type": "tool_execution_end", "toolCallId": "t1", "toolName": "uo_read", "isError": False})
    out({"type": "message_end", "message": {"role": "assistant", "model": "fake", "stopReason": "stop",
         "content": [{"type": "text", "text": "echo: " + body}],
         "usage": {"input": 10, "cacheRead": 5, "cacheWrite": 0, "output": 7, "cost": {"total": 0.01}}}})
''')


def fake_omp() -> list:
    path = os.path.join(tempfile.mkdtemp(prefix="nystul_fake_"), "fake_omp.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(FAKE_OMP)
    return [sys.executable, path]


def wait_done(n: nystul.Nystul, conv: int, timeout=20.0) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        m = n.get(conv)["messages"][-1]
        if m["status"] != "running":
            return m
        time.sleep(0.05)
    return n.get(conv)["messages"][-1]


def status_of(fn) -> int:
    try:
        fn()
    except nystul.NystulError as e:
        return e.code
    return 200


# ---------------------------------------------------------------------------- tools

def test_tools():
    print("== nystul_tools: confinement and read-only surface ==")
    for path, want in (("ClassicUO/settings.json", "is sealed"), ("harness/data/telegram.json", "is sealed"),
                       ("harness/data/harness.db", "is sealed"), (".git/config", "is sealed"),
                       ("../outside.txt", "outside the repository")):
        ok, text = nt.run("uo_read", {"path": path})
        check(f"read {path} refused", not ok and want in text, text)
    ok, text = nt.run("uo_list", {"path": "harness/data"})
    check("list hides sealed files", ok and "telegram.json" not in text and ".db" not in text, text[:200])
    ok, text = nt.run("uo_read", {"path": "README.md", "start": 1, "end": 3})
    lines = text.splitlines()
    check("read returns numbered lines start..end", ok and len(lines) == 3 and lines[0].startswith("1: ")
          and lines[2].startswith("3: "), text)
    ok, text = nt.run("uo_grep", {"pattern": "rule 0", "glob": "AGENTS.md"})
    check("grep is case-insensitive with relpath:line", ok and text.startswith("AGENTS.md:"), text[:200])

    db = os.path.join(tempfile.mkdtemp(), "harness.db")
    memory.connect(db).close()
    os.environ["NYSTUL_MEMORY_DB"] = db
    os.environ["NYSTUL_DATA"] = tempfile.mkdtemp()
    try:
        rec = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 10) SELECT x FROM c"
        ok, text = nt.run("uo_sql", {"db": "harness", "query": rec, "limit": 3})
        check("select with row-limit footer", ok and text.splitlines() == ["x", "1", "2", "3",
                                                                          "(3 rows shown; more available)"], text)
        ok, text = nt.run("uo_sql", {"db": "harness", "query": "PRAGMA table_info(meta)"})
        check("PRAGMA table_info allowed", ok and "name" in text.splitlines()[0], text)
        for q in ("INSERT INTO meta(key, value) VALUES ('x', 'y')", "DELETE FROM meta",
                  "ATTACH 'x.db' AS y", "PRAGMA journal_mode=DELETE", "CREATE TABLE z(a)"):
            ok, text = nt.run("uo_sql", {"db": "harness", "query": q})
            check(f"refused: {q}", not ok and text.startswith("error:"), text)
        con = sqlite3.connect(db)
        check("store unchanged", con.execute("SELECT count(*) FROM meta WHERE key='x'").fetchone()[0] == 0
              and con.execute("SELECT count(*) FROM sqlite_master WHERE name='z'").fetchone()[0] == 0)
        con.close()
        ok, text = nt.run("uo_sql", {"db": "discord", "query": "SELECT 1"})
        check("missing discord db", not ok and "not on this computer" in text, text)
        ok, text = nt.run("uo_discord", {"query": "lumber"})
        check("missing discord kb", not ok and "not on this computer" in text, text)
        ok, text = nt.run("uo_ctl", {"command": "act", "args": ["say", "hello"]})
        check("ctl act refused", not ok and "not allowed" in text, text)
        ok, text = nt.run("uo_ctl", {"command": "know", "args": ["search", "x"]})
        check("ctl know refused (it posts to the Seer's bus)", not ok and "not allowed" in text, text)
        ok, text = nt.run("uo_ctl", {"command": "runes", "args": ["libraries"]})
        check("ctl runes libraries allowed", "not allowed" not in text and "runes needs" not in text, text[:200])
        ok, text = nt.run("uo_ctl", {"command": "runes", "args": ["add"]})
        check("ctl runes op allowlist", not ok and "runes needs" in text, text)
    finally:
        os.environ.pop("NYSTUL_MEMORY_DB", None)
        os.environ.pop("NYSTUL_DATA", None)
    for route in ("/api/playback", "/api/chat", "/x/api/state", "/api/state/../chat"):
        ok, text = nt.run("uo_api", {"route": route})
        check(f"route {route} refused", not ok and "route not allowed" in text, text)


# ---------------------------------------------------------------------------- parser

def test_parse_event():
    print("== parse_event over a canned omp stream ==")
    run = nystul.Run(1, 1, 2, "q")
    stream = [
        "not json",
        json.dumps({"type": "session", "id": "s"}),
        json.dumps({"type": "message_start", "message": {"role": "assistant"}}),
        json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "Ah, "}}),
        json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "friend"}}),
    ]
    for line in stream:
        nystul.parse_event(run, line)
    check("partial text streams", run.partial == "Ah, friend", run.partial)
    for line in (
        json.dumps({"type": nystul.EV_TOOL_START, "toolCallId": "a", "toolName": "uo_discord", "args": {"query": "x"}}),
        json.dumps({"type": nystul.EV_TOOL_START, "toolCallId": "b", "toolName": "uo_api",
                    "args": {"route": "/api/state"}}),
        # parallel calls: the first one ends first (live 2026-10-08 the flags came out swapped)
        json.dumps({"type": nystul.EV_TOOL_END, "toolCallId": "a", "toolName": "uo_discord", "isError": True}),
        json.dumps({"type": nystul.EV_TOOL_END, "toolCallId": "b", "toolName": "uo_api", "isError": False}),
        json.dumps({"type": "message_end", "message": {
            "role": "assistant", "model": "m", "stopReason": "stop", "content": [{"type": "text", "text": "Done."}],
            "usage": {"input": 100, "cacheRead": 50, "cacheWrite": 10, "output": 20, "cost": {"total": 0.02}}}}),
        json.dumps({"type": "message_end", "message": {
            "role": "assistant", "stopReason": "stop", "content": [],
            "usage": {"input": 1, "output": 1, "cost": {"total": 0.01}}}}),
    ):
        nystul.parse_event(run, line)
    check("parallel steps keep their own result", [(s["tool"], s["args"], s["ok"]) for s in run.steps]
          == [("uo_discord", {"query": "x"}, False), ("uo_api", {"route": "/api/state"}, True)], str(run.steps))
    check("cost and tokens summed", abs(run.cost - 0.03) < 1e-9 and run.tokens_in == 161 and run.tokens_out == 21,
          f"{run.cost} {run.tokens_in} {run.tokens_out}")
    check("final = last non-empty assistant text", run.final == "Done." and run.model == "m", run.final)


# ---------------------------------------------------------------------------- runs

def test_runs(omp):
    print("== runs against a fake omp ==")
    db = os.path.join(tempfile.mkdtemp(), "nystul.db")
    n = nystul.Nystul(db, omp_cmd=omp, context=lambda: {"mode": "replay", "session": "T"})
    try:
        check("no store file before the first ask", n.list()["conversations"] == [] and not os.path.exists(db))
        r = n.ask(None, "  where is the first   question?  ")
        m = wait_done(n, r["conversation"])
        check("answer done with the question in its prompt", m["status"] == "done"
              and "where is the first   question?" in m["text"] and "Viz: replay T" in m["text"], m["status"])
        check("steps, cost, tokens stored", m["steps"][0]["tool"] == "uo_read" and m["steps"][0]["ok"] is True
              and m["cost"] == 0.01 and m["tokens_in"] == 15 and m["tokens_out"] == 7 and m["model"] == "fake",
              str({k: m[k] for k in ("cost", "tokens_in", "tokens_out", "model")}))
        lst = n.list()["conversations"]
        check("listed with a collapsed title", lst[0]["title"] == "where is the first question?"
              and lst[0]["messages"] == 2 and lst[0]["running"] is False, str(lst))
        n.ask(r["conversation"], "and the second?")
        m2 = wait_done(n, r["conversation"])
        check("transcript carried into the next prompt", m2["status"] == "done"
              and "**Operator:** where is the first   question?" in m2["text"]
              and "**Nystul:** echo:" in m2["text"], m2["text"][:300])
        check("400 on empty / too long", status_of(lambda: n.ask(None, "   ")) == 400
              and status_of(lambda: n.ask(None, "x" * (nystul.MAX_CHARS + 1))) == 400)
        check("404 on unknown conversation", status_of(lambda: n.ask(999, "hi")) == 404)

        s = n.ask(None, "SLOW one")
        check("409 while the conversation runs", status_of(lambda: n.ask(s["conversation"], "again")) == 409)
        time.sleep(0.3)
        live = n.get(s["conversation"])["messages"][-1]
        check("running message reported", live["status"] == "running" and n.list()["conversations"][0]["running"])
        s2 = n.ask(None, "SLOW two")
        check(f"MAX_RUNS={nystul.MAX_RUNS}: a third run is 409", status_of(lambda: n.ask(None, "SLOW three")) == 409)
        check("cancel", n.cancel(s["conversation"]) and n.cancel(s2["conversation"]))
        mc = wait_done(n, s["conversation"])
        check("cancelled status", mc["status"] == "cancelled", mc["status"])
        check("cancel with nothing running", not n.cancel(s["conversation"]))

        f = n.ask(None, "please FAIL")
        mf = wait_done(n, f["conversation"])
        check("failing run kept as error with stderr", mf["status"] == "error" and "boom" in (mf["error"] or ""),
              str(mf["error"]))
        wait_done(n, s2["conversation"])
    finally:
        n.close()

    con = sqlite3.connect(db)
    con.execute("UPDATE messages SET status='running' WHERE id=(SELECT max(id) FROM messages)")
    con.commit()
    con.close()
    n2 = nystul.Nystul(db, omp_cmd=omp)
    try:
        last = n2.get(f["conversation"])["messages"][-1]
        check("a running row from a dead viz is marked interrupted", last["status"] == "error"
              and "interrupted" in last["error"], str(last["error"]))
    finally:
        n2.close()


# ---------------------------------------------------------------------------- HTTP

def http(method, url, body=None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, method=method, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http(omp, logdir):
    print("== /api/nystul routes ==")
    tmp = tempfile.mkdtemp()
    port = tv._free_ports(1)[0]
    srv = viz_server.VizServer(("127.0.0.1", port), viz_feed.ReplayDriver(tv.TAG, logdir),
                               os.path.join(tv.ROOT, "viz", "dist"), os.path.join(tmp, "harness.db"),
                               nystul_db=os.path.join(tmp, "nystul.db"))
    srv.nystul.close()
    srv.nystul = nystul.Nystul(os.path.join(tmp, "nystul.db"), omp_cmd=omp)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        code, empty = http("GET", base + "/api/nystul")
        check("empty list before any ask", code == 200 and empty["conversations"] == [] and empty["available"])
        code, r = http("POST", base + "/api/nystul/ask", {"conversation": None, "text": "hi"})
        check("ask 200 with ids", code == 200 and r["ok"] and isinstance(r["conversation"], int)
              and isinstance(r["message"], int), str(r))
        conv = r["conversation"]
        end = time.monotonic() + 20
        while time.monotonic() < end:
            code, c = http("GET", f"{base}/api/nystul/{conv}")
            if c["messages"][-1]["status"] != "running":
                break
            time.sleep(0.05)
        check("conversation has 2 messages, answered", code == 200 and [m["role"] for m in c["messages"]]
              == ["user", "nystul"] and c["messages"][1]["status"] == "done", str(c)[:200])
        code, lst = http("GET", base + "/api/nystul")
        check("listed", code == 200 and [x["id"] for x in lst["conversations"]] == [conv])
        code, e = http("POST", base + "/api/nystul/ask", {"conversation": None, "text": ""})
        check("empty text 400", code == 400 and not e["ok"], str(e))
        code, e = http("POST", base + "/api/nystul/ask", {"conversation": None, "text": 5})
        check("non-string text 400", code == 400, str(e))
        code, e = http("POST", base + "/api/nystul/ask", {"conversation": 4242, "text": "hi"})
        check("unknown conversation 404", code == 404, str(e))
        code, e = http("GET", base + "/api/nystul/abc")
        check("non-integer id 400", code == 400, str(e))
        code, e = http("GET", base + "/api/nystul/4242")
        check("unknown id 404", code == 404, str(e))
        code, e = http("POST", base + "/api/nystul/cancel", {"conversation": conv})
        check("cancel with nothing running 404", code == 404 and not e["ok"], str(e))
    finally:
        srv.shutdown()
        srv.server_close()


def main():
    omp = fake_omp()
    test_tools()
    test_parse_event()
    test_runs(omp)
    test_http(omp, tv.pinned_capture())
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    sys.exit(0 if not FAILURES else 1)


if __name__ == "__main__":
    main()

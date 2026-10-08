"""Backend tests for the visualizer (docs/VISUALIZER.md §7): viz_feed + viz_server.

  python harness/test_viz.py

- replay parity on session 20260929_163420 (pinned to the commit that
  recorded the bank errand, since the live proxy may still append to the file):
  exact order, errand outcome, proxy events, /api/state == SessionTap.state()
- order fallback: 20260928_141253 (pre-fix S2C rows) replays as "approx"
- SSE framing (one `world_events` batch per write + the newest state),
  monotonic seqs, verbatim envelopes, Last-Event-ID resume without
  duplicates, and state coalescing
- ping-pong burst the size of session 20261005_093927's (624 pumps, ~15000
  events): a stalled reader's mailbox stays bounded and its next write is the
  newest state; a 4 MB/s reader over HTTP has the newest state <1 s after the
  burst, with every speech/intent/cliloc/gump event, in order
- live: viz_server --live against a proxy subprocess (fake upstream, private
  ports), started before the proxy (proxy down -> connected=false); asserts
  the viz never connects to the proxy's control port and adds no C2S packets
- agent gate via /api/gate: replay 409, rearm 400, proxy down 502; live pause
  (proxy reports paused, control-port injection gets ERR ...paused), resume,
  kill, resume-while-killed 409
- agent intent: lumber spot + per-wood tally kept, malformed ones dropped, a
  tally update stays one step
- GET /api/lumber/grove: a spot's area and trees with their harvest-memory
  states, by spot id or by a position inside it; bad params 400
- job analytics (harness/jobs.py): trip rows, totals, per-day split, rolling
  logs/hr, deaths by cause, thefts, value from woods and the board price
  history (as of each trip's end), since filter, determinism
- /api/jobs, /api/overseer (cursors, newest-200 window, open junctures,
  heartbeat), POST /api/chat (stored as a user row; empty / whitespace / too
  long / non-string -> 400); GET/POST /api/captcha (default human, written
  where the runner's Memory reads it, bad modes -> 400); a missing memory
  store is not created by GETs; live mode serves the same routes with the
  proxy down
"""
import collections
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
sys.path.insert(0, HERE)

import jobs  # noqa: E402
import memory  # noqa: E402
import proxy  # noqa: E402
import viz_feed  # noqa: E402
import viz_server  # noqa: E402
from uo.s2c import encode_packet  # noqa: E402

TAG = "20260929_163420"
TAG_COMMIT = "5f8c228"      # "Phase 3 DONE: unattended bank run ... session_20260929_163420"
OLD_TAG = "20260928_141253"


def _free_ports(n):
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return ports


(SERVER_PORT, SSE_PORT, BURST_PORT, OVERSEER_PORT, OVERSEER_PORT2, OVERSEER_PORT3,
 PROXY_PORT, UPSTREAM_PORT, CONTROL_PORT, STATE_PORT, VIZ_PORT) = _free_ports(11)
C2S_KEY, S2C_KEY = 0x0F, 0x5A
PRELUDE = bytes([0xFF, 0x00, 0x0D] + [0] * 7 + [0x0C, S2C_KEY, C2S_KEY])
SELF = 0x00094375

FAILURES = []


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAILURES.append(name)


def pinned_capture() -> str:
    """logdir holding the pinned 163420 capture."""
    d = tempfile.mkdtemp(prefix="viz_test_")
    for ext in ("jsonl", "c2s.raw", "s2c.raw"):
        data = subprocess.run(["git", "show", f"{TAG_COMMIT}:logs/session_{TAG}.{ext}"], cwd=ROOT,
                              capture_output=True, check=True).stdout
        with open(os.path.join(d, f"session_{TAG}.{ext}"), "wb") as f:
            f.write(data)
    return d


def get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def post(url, body: dict, timeout=5) -> tuple[int, dict]:
    """POST JSON; (status, parsed reply) for 2xx and HTTP errors alike."""
    req = urllib.request.Request(url, method="POST", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def state_port(req: dict) -> dict:
    """One request straight to the test proxy's state port (bypassing the viz)."""
    with socket.create_connection(("127.0.0.1", STATE_PORT), timeout=5) as s:
        s.sendall((json.dumps(req) + "\n").encode())
        with s.makefile("rb") as f:
            return json.loads(f.readline())


def recv_exact(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        d = sock.recv(n - len(buf))
        if not d:
            raise ConnectionError("closed")
        buf += d
    return buf


def wait_state(base: str, pred, timeout=3.0) -> bool:
    """Poll the viz's /api/state until pred(state) holds."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred(get(base + "/api/state")):
            return True
        time.sleep(0.1)
    return False


def seeded_memory_db() -> str:
    """A temp harness memory store with one confirmed move and one deny."""
    path = os.path.join(tempfile.mkdtemp(), "harness.db")
    con = memory.connect(path)
    memory._upsert_walk(con.cursor(), [(0, 10, 10, 0, 2, 1, 1.0), (0, 11, 10, 0, 2, 0, 2.0)])
    con.commit()
    con.close()
    return path


MEMORY_DB = seeded_memory_db()


def serve(feed, port, memory_db=None):
    srv = viz_server.VizServer(("127.0.0.1", port), feed, os.path.join(ROOT, "viz", "dist"), memory_db or MEMORY_DB)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return srv


def get_status(url, timeout=5) -> tuple[int, dict]:
    """GET JSON; (status, parsed reply) for 2xx and HTTP errors alike."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


DAY = 86400.0
WOODS = {"ordinary": {"name": "ordinary", "value_gp": 9.5}, "goldenwood": {"name": "goldenwood", "value_gp": None}}


def seed_jobs(m: memory.Memory):
    """Three lumber trips over two UTC days (1970-01-01/02), an errand row, job events
    of every kind, harvest attempts, chat, junctures and an overseer heartbeat."""
    m.episode("lumber", {"trip": 1, "t_start": 1000.0, "t_end": 2800.0, "logs": 20, "stored": 20, "captchas": 1,
                         "captcha_wait_s": 12.5, "attempts": 10, "successes": 4,
                         "phases_s": {"harvest": 1500.0, "convert": 60.0, "to_room": 200.0, "store": 20.0, "exit": 20.0}})
    m.episode("errand", {"t_start": 1500.0, "t_end": 1600.0, "logs": 999})
    m.episode("lumber", {"trip": 2, "t_start": 5000.0, "t_end": 6200.0, "logs": 30, "stored": 25,
                         "woods": {"ordinary": 20, "goldenwood": 10}, "attempts": 12, "successes": 6})
    m.episode("lumber", {"trip": 1, "t_start": DAY + 100, "t_end": DAY + 700, "logs": 12, "stored": 12, "stockpiled": 12})
    m.job_event("lumber", "death", {"cause": "pk", "name": "Bob"}, 0, 10, 20, t=1500.0)
    m.job_event("lumber", "death", {"cause": "mob"}, t=5100.0)
    m.job_event("lumber", "death", {"cause": "fall"}, t=7000.0)
    m.job_event("lumber", "theft", {"items": {"board": 7}}, t=6000.0)
    m.job_event("lumber", "theft", {"amount": 5}, t=DAY + 200)
    m.job_event("lumber", "pk_seen", {"name": "Bob"}, t=1400.0)
    m.job_event("lumber", "pk_seen", {}, t=DAY + 300)
    m.job_event("lumber", "flee", {"reason": "pk"}, t=1450.0)
    m.job_event("mining", "death", {"cause": "pk"}, t=1600.0)
    for i, (outcome, amount) in enumerate([("success", 5), ("success", 6), ("fail", 0), ("success", 7),
                                           ("fail", 0), ("depleted", 0)]):
        m.harvest_record(0, 100 + i, 200, 0, 0x0CD0, outcome, amount, t=1100.0 + i)


def test_intent_fields():
    print("== agent intent: lumber spot and per-wood tally (proxy.SessionTap.set_intent) ==")
    tap = proxy.SessionTap(viz_feed._Null(), viz_feed._Null(), viz_feed._Null())
    base = {"text": "Chopping by 1,2 (3/15 logs)", "kind": "chop", "loop": "lumber", "trip": 1, "trips": 2,
            "target": [1, 2]}
    err = tap.set_intent({**base, "spot": "horseshoe_bay", "woods": {"ordinary": 2, "oak": 1}})
    check("spot and woods kept", err is None and tap.intent.get("spot") == "horseshoe_bay"
          and tap.intent.get("woods") == {"ordinary": 2, "oak": 1}, f"{err} {tap.intent}")
    tap.set_intent({**base, "spot": "horseshoe_bay", "woods": {"ordinary": 3, "oak": 1}})
    check("a tally update replaces the intent and stays one step",
          tap.intent.get("woods") == {"ordinary": 3, "oak": 1} and len(tap.intents) == 1
          and "until" not in tap.intents[-1], f"{tap.intent} {tap.intents}")
    bad = [{"oak": -1}, {"oak": "2"}, {"": 1}, {"oak": True}, ["oak"], {f"w{i}": 1 for i in range(33)}]
    for woods in bad:
        tap.set_intent({**base, "woods": woods})
        check(f"malformed woods dropped: {str(woods)[:40]}", "woods" not in tap.intent, str(tap.intent))
    tap.set_intent({**base, "spot": "s" * 65})
    check("a too-long spot id dropped", "spot" not in tap.intent, str(tap.intent))


def test_jobs():
    print("== job analytics (harness/jobs.py) ==")
    path = os.path.join(tempfile.mkdtemp(), "harness.db")
    m = memory.Memory(path)
    seed_jobs(m)
    a = jobs.analytics(m, "lumber", 0, woods=WOODS)
    t = a["totals"]
    check("totals: 3 lumber trips (errand row ignored), 62 logs, 57 stored",
          (t["trips"], t["logs"], t["stored"]) == (3, 62, 57), str({k: t[k] for k in ("trips", "logs", "stored")}))
    check("boards stored: only the stockpiled ones (trip #3's 12; the chest-era trips' 45 put away don't count), "
          "per trip and on day 2",
          (t["stockpiled"], [r["stockpiled"] for r in a["trips"]], [d["stockpiled"] for d in a["days"]])
          == (12, [0, 0, 12], [0, 12]), str((t["stockpiled"], [d["stockpiled"] for d in a["days"]])))
    check("active time = sum of trip durations (1800+1200+600 s = 1 h), gaps excluded",
          t["active_s"] == 3600.0 and t["active_hours"] == 1.0, f"{t['active_s']} {t['active_hours']}")
    check("logs/hr 62.0, logs/trip 20.67, chop success 10/22",
          (t["logs_per_hour"], t["logs_per_trip"], t["success_rate"]) == (62.0, 20.67, 0.45), str(t))
    check("captchas 1, 12.5 s wait", (t["captchas"], t["captcha_wait_s"]) == (1, 12.5))
    check("deaths by cause: pk 1, mob 1, other ('fall') 1; other jobs' events excluded",
          t["deaths"] == {"pk": 1, "mob": 1, "other": 1, "total": 3}, str(t["deaths"]))
    check("thefts: 2, loss 7 (items) + 5 (amount) = 12", t["thefts"] == {"count": 2, "amount": 12, "items": {"board": 7}},
          str(t["thefts"]))
    check("pk_seen 2, flees 1", (t["pk_seen"], t["flees"]) == (2, 1))
    check("value: 52 ordinary logs x 9.5 (unbroken-down trips count as ordinary) = 494, 10 goldenwood unpriced",
          t["value_gp"] == 494.0 and t["value_unpriced_logs"] == 10, f"{t['value_gp']} {t['value_unpriced_logs']}")
    check("wood breakdown from the rows that carry one", t["woods"] == {"ordinary": 20, "goldenwood": 10}, str(t["woods"]))
    check("span first/last", (t["first_t"], t["last_t"]) == (1000.0, DAY + 700), str((t["first_t"], t["last_t"])))
    tr = a["trips"]
    check("trip rows in start order: n, duration, logs/hr",
          [(r["n"], r["duration_s"], r["logs_per_hour"]) for r in tr] == [(1, 1800.0, 40.0), (2, 1200.0, 90.0),
                                                                          (3, 600.0, 72.0)],
          str([(r["n"], r["duration_s"], r["logs_per_hour"]) for r in tr]))
    check("trip phases and value per trip", tr[0]["phases_s"]["harvest"] == 1500.0
          and [r["value_gp"] for r in tr] == [190.0, 190.0, 114.0], str([r["value_gp"] for r in tr]))
    check("events attributed to the trip they fell in (pk death in #1; mob death + theft in #2)",
          [r["events"] for r in tr] == [{"death": 1, "pk_seen": 1, "flee": 1}, {"death": 1, "theft": 1},
                                        {"theft": 1, "pk_seen": 1}],
          str([r["events"] for r in tr]))
    days = {d["day"]: d for d in a["days"]}
    check("per-day (UTC): 1970-01-01 two trips 50 logs 3000 s = 60/hr; 01-02 one trip",
          list(days) == ["1970-01-01", "1970-01-02"] and days["1970-01-01"]["logs"] == 50
          and days["1970-01-01"]["logs_per_hour"] == 60.0 and days["1970-01-02"]["trips"] == 1, str(list(days)))
    check("per-day events: day 1 has all three deaths, day 2 the 5-loss theft",
          days["1970-01-01"]["deaths"]["total"] == 3 and days["1970-01-02"]["thefts"]["amount"] == 5
          and days["1970-01-02"]["deaths"]["total"] == 0)
    shifted = jobs.analytics(m, "lumber", 0, woods=WOODS, utc_offset_s=23 * 3600)
    check("utc offset moves the day boundary (+23 h: trip #2 lands on 01-02)",
          [(d["day"], d["trips"]) for d in shifted["days"]] == [("1970-01-01", 1), ("1970-01-02", 2)],
          str([(d["day"], d["trips"]) for d in shifted["days"]]))
    roll = [(p["n"], p["logs_per_hour"], p["cum_logs_per_hour"], p["window_trips"]) for p in a["rolling"]]
    check("rolling 1 h window at each trip end: 40 -> (20+30)/3000 s = 60 -> #3 alone 72; cumulative 40, 60, 62",
          roll == [(1, 40.0, 40.0, 1), (2, 60.0, 60.0, 2), (3, 72.0, 62.0, 1)], str(roll))
    check("harvest outcomes: 3 success (yield 18), 2 fail, 1 depleted, 60% land",
          {k: a["harvest"][k] for k in ("success", "fail", "depleted", "yield", "success_rate")}
          == {"success": 3, "fail": 2, "depleted": 1, "yield": 18, "success_rate": 0.6}, str(a["harvest"]))
    check("timeline: 8 lumber events in time order",
          [e["kind"] for e in a["events"]] == ["pk_seen", "flee", "death", "death", "theft", "death", "theft", "pk_seen"],
          str([e["kind"] for e in a["events"]]))
    check("wood rows: ordinary priced, goldenwood known without a value",
          [(w["name"], w["logs"], w["total_gp"]) for w in a["woods"]] == [("goldenwood", 10, None), ("ordinary", 20, 190.0)],
          str(a["woods"]))
    since = jobs.analytics(m, "lumber", 4000, woods=WOODS)
    check("since=4000: trips #2 and #3, events from t>=4000",
          since["totals"]["trips"] == 2 and since["totals"]["logs"] == 42 and since["totals"]["deaths"]["total"] == 2
          and since["harvest"]["success"] == 0, str(since["totals"]["trips"]))
    upto = jobs.analytics(m, "lumber", 0, 5000, woods=WOODS)
    check("until=5000 is exclusive: trip #1 only (trip #2 starts at 5000), events and attempts before it",
          (upto["totals"]["trips"], upto["totals"]["deaths"]["total"], upto["totals"]["pk_seen"],
           upto["harvest"]["success"], upto["until"]) == (1, 1, 1, 3, 5000),
          str((upto["totals"]["trips"], upto["totals"]["deaths"], upto["harvest"])))
    window = jobs.analytics(m, "lumber", 4000, DAY, woods=WOODS)
    check("[4000, DAY): trip #2, its death and theft and the fall at 7000; nothing from day 2",
          (window["totals"]["trips"], window["totals"]["deaths"]["total"], window["totals"]["thefts"]["count"],
           [d["day"] for d in window["days"]]) == (1, 2, 1, ["1970-01-01"]), str(window["totals"]))
    no_start = [{"logs": 5}]
    check("a trip without t_start: only in the unbounded range",
          [jobs.compute(no_start, [], None, None, since=s, until=u)["totals"]["trips"]
           for s, u in ((0, None), (1, None), (0, 10_000))] == [1, 0, 0])
    nowoods = jobs.analytics(m, "lumber", 0, woods=None)
    check("no woods.json: value null, every log unpriced",
          nowoods["totals"]["value_gp"] is None and nowoods["totals"]["value_unpriced_logs"] == 62
          and nowoods["woods_file"] is False)
    again = jobs.analytics(m, "lumber", 0, woods=WOODS)
    check("deterministic: same store, same answer", json.dumps(again, sort_keys=True) == json.dumps(a, sort_keys=True))
    empty = jobs.compute([], [], [], None)
    check("empty data: zeros and nulls, no division errors",
          empty["totals"]["trips"] == 0 and empty["totals"]["logs_per_hour"] is None
          and empty["totals"]["logs_per_trip"] is None and empty["rolling"] == [] and empty["days"] == []
          and empty["harvest"]["success_rate"] is None, str(empty["totals"]))
    check("theft item shapes", jobs.theft_loss({"items": [{"name": "board", "amount": 4}, {"graphic": "0x1BDD"}, "axe"]})
          == (6, {"board": 4, "0x1BDD": 1, "axe": 1}))
    missing = jobs.load_woods(os.path.join(tempfile.mkdtemp(), "woods.json"))
    check("load_woods: absent file -> None", missing is None)
    # board price history: each trip at the newest board:<wood> price at or before its t_end; before
    # a wood's first price, that first price
    m.price_record("board:ordinary", 20.0, "vendor search", t=2800.5)   # just after trip #1 ended
    m.price_record("board:ordinary", 30.0, "vendor search", t=6200.0)   # exactly trip #2's t_end
    m.price_record("board:ordinary", 25.0, "vendor search", t=DAY)      # before trip #3
    m.price_record("board:goldenwood", 50.0, "vendor search", t=DAY + 800)     # after every trip
    m.price_record("hatchet:copper", 999.0, "vendor search", t=0.0)     # not a board: ignored
    h = jobs.analytics(m, "lumber", 0, woods=WOODS)
    check("price history: #1 before any price -> the first one (not woods.json) 20 x 20 = 400; #2 at its t_end "
          "(inclusive) 20 x 30 + goldenwood at its first (later) price 10 x 50 = 1100; #3 12 x 25 = 300",
          [r["value_gp"] for r in h["trips"]] == [400.0, 1100.0, 300.0]
          and (h["totals"]["value_gp"], h["totals"]["value_unpriced_logs"]) == (1800.0, 0),
          str([r["value_gp"] for r in h["trips"]]))
    check("trip rows say which price they used",
          h["trips"][0]["board_prices"] == {"ordinary": {"gp": 20.0, "t": 2800.5, "source": "vendor search"}}
          and h["trips"][1]["board_prices"] == {"ordinary": {"gp": 30.0, "t": 6200.0, "source": "vendor search"},
                                                "goldenwood": {"gp": 50.0, "t": DAY + 800, "source": "vendor search"}},
          str([r["board_prices"] for r in h["trips"]]))
    check("wood rows: price now = newest row (ordinary 25, goldenwood 50); totals from the trips' own prices",
          [(w["name"], w["value_gp"], w["price_t"], w["total_gp"]) for w in h["woods"]]
          == [("goldenwood", 50.0, DAY + 800, 500.0), ("ordinary", 25.0, DAY, 600.0)], str(h["woods"]))
    check("price_history keeps every observation, oldest first",
          [(r["item"], r["price_gp"]) for r in m.price_history("board:")]
          == [("board:ordinary", 20.0), ("board:ordinary", 30.0), ("board:ordinary", 25.0), ("board:goldenwood", 50.0)])
    m.close()


def test_lumber_travel():
    print("== lumber travel, time split and supplies (harness/jobs.py) ==")
    tome = "0x546ACD06"                      # the Cambria tome holding Witcher rune 291 (data/witcher.json)
    row = {"loop": "lumber", "spot": "w", "trip": 1, "outcome": "stored", "t_start": 1000.0, "t_end": 2000.0,
           "phases_s": {"harvest": 900.0, "to_room": 80.0, "convert": 10.0, "store": 10.0},
           "walk_out_s": 100.0, "lockout_s": 60.0, "chop_s": 500.0, "logs": 200, "travel_s": 50.0,
           "supplies": {"library_charges": 1, "own_charges": 1, "recall_casts": 1, "reagents_used": {"mandrake root": 1}}}
    events = [  # a row from before 2026-10-03: no book, the rune only in the tome row's name
        {"t": 900.0, "kind": "travel", "data": {"leg": "out", "rune": 15, "name": "291 - Hidden Valley (Outside South)",
                                                "ok": True, "method": "charge", "charges": 38, "elapsed_s": 2.2}},
        {"t": 1010.0, "kind": "travel", "data": {"leg": "out", "witcher_rune": "291", "book": tome, "ok": True,
                                                 "s": 30.0, "walk_s": 28.0, "charges": 37,
                                                 "tries": [{"method": "charge", "ok": True, "failure": None}]}},
        {"t": 1900.0, "kind": "travel", "data": {"leg": "home", "book": "0x40000001", "ok": True, "s": 20.0,
                                                 "charges": 5, "tries": [{"method": "charge", "ok": False,
                                                                          "failure": "disturbed"},
                                                                         {"method": "spell", "ok": True, "failure": None}]}},
        {"t": 1950.0, "kind": "travel", "data": {"leg": "home", "book": "0x40000001", "ok": False, "s": 1.0,
                                                 "failure": "recall: the book's gump didn't open", "tries": []}}]
    a = jobs.compute([row], events, [], None)
    legs = {g["leg"]: g for g in a["travel"]["legs"]}
    check("legs: out 2 landed (mean 30 s, the walk to the library 28 s); home 2, 1 landed, 2 casts, a charge "
          "and a spell, failures by reason",
          (legs["out"]["n"], legs["out"]["ok"], legs["out"]["mean_s"], legs["out"]["mean_walk_s"]) == (2, 2, 16.1, 28.0)
          and (legs["home"]["n"], legs["home"]["ok"], legs["home"]["casts"], legs["home"]["charge"],
               legs["home"]["spell"]) == (2, 1, 2, 1, 1)
          and legs["home"]["failures"] == {"disturbed": 1, "recall: the book's gump didn't open": 1}, str(legs))
    books = {b["book"]: b for b in a["travel"]["books"]}
    check("the library tome's charges over time, the old row's tome found from its rune; our book apart",
          books[tome]["charges"] == [[900.0, 38], [1010.0, 37]] and books[tome]["runes"] == ["291"]
          and books[tome]["last_charges"] == 37 and not books[tome]["own"]
          and books["0x40000001"]["own"] and books["0x40000001"]["last_charges"] == 5, str(books))
    tr = a["trips"][0]
    check("time split: travel 50, lockout 60, field 900-100-60 = 740, the rest 150 (sums to the 1000 s trip)",
          tr["time_split"] == {"travel": 50.0, "lockout": 60.0, "field": 740.0, "other": 150.0}
          and tr["field_logs_per_hour"] == round(200 * 3600 / 740, 2), str(tr["time_split"]))
    check("supplies summed over the trips that record them",
          a["supplies"] == {"trips": 1, "library_charges": 1, "own_charges": 1, "recall_casts": 1,
                            "reagents_used": {"mandrake root": 1}}, str(a["supplies"]))


def test_hunt_jobs():
    print("== hunt analytics (harness/jobs.py compute_hunt) ==")
    path = os.path.join(tempfile.mkdtemp(), "harness.db")
    m = memory.Memory(path)
    # visit 1 (1000-2800 s): 2 kills, both looted (an old row without xp/name/mob, a new one);
    # visit 2 (5000-5900 s, day 1): 1 kill never looted, a mob death, a leave;
    # a kill + loot at 7000 s that no visit row covers (run stopped mid-visit);
    # visit 3 on day 2: an orc, its loot names it.
    m.episode("hunt", {"visit": 1, "t_start": 1000.0, "t_end": 2800.0, "kills": 2, "gold": 50, "hits_lost": 40,
                       "casts": 9, "heals": 2, "potions": 1, "ended": "done", "spell": "Lightning"})
    m.episode("hunt", {"visit": 2, "t_start": 5000.0, "t_end": 5900.0, "kills": 1, "gold": 0, "ended": "hits low"})
    m.episode("hunt", {"visit": 1, "t_start": DAY + 100, "t_end": DAY + 1000, "kills": 1, "gold": 120})
    m.episode("lumber", {"trip": 1, "t_start": 1500.0, "t_end": 1600.0, "logs": 9})
    m.job_event("hunt", "kill", {"serial": "0x01", "name": "a mongbat"}, t=1100.0)
    m.job_event("hunt", "kill", {"serial": "0x02", "name": "a mongbat"}, t=1200.0)
    m.job_event("hunt", "loot", {"corpse": "0x41", "gold": 20,
                                 "items": [{"graphic": "0x0EED", "amount": 20}, {"graphic": "0x0F0C", "amount": 1}]},
                t=1210.0)
    m.job_event("hunt", "loot", {"corpse": "0x42", "mob": "0x01", "name": "a mongbat", "gold": 30, "xp": 33},
                t=1300.0)
    m.job_event("hunt", "kill", {"serial": "0x03", "name": "a mongbat"}, t=5100.0)
    m.job_event("hunt", "death", {"cause": "mob"}, t=5800.0)
    m.job_event("hunt", "leave", {"why": "hits low"}, t=5850.0)
    m.job_event("hunt", "speech_hold", {"speakers": []}, t=5200.0)
    m.job_event("hunt", "speech_clear", {"waited_s": 12.5}, t=5212.5)
    m.job_event("hunt", "kill", {"serial": "0x04", "name": "a mongbat"}, t=7000.0)
    m.job_event("hunt", "loot", {"mob": "0x04", "name": "a mongbat", "gold": 25, "xp": 25}, t=7010.0)
    m.job_event("hunt", "kill", {"serial": "0x05", "name": "an orc"}, t=DAY + 200)
    m.job_event("hunt", "loot", {"mob": "0x05", "name": "an orc", "gold": 110, "xp": 120}, t=DAY + 210)
    m.job_event("lumber", "death", {"cause": "pk"}, t=1500.0)
    a = jobs.analytics(m, "hunt", 0)
    t = a["totals"]
    check("hunt totals from the events: 5 kills, 4 looted, gold 185, xp 20 (old row: gold piles taken) + 33 + 25 + 120",
          (t["kills"], t["looted"], t["gold"], t["xp"], t["xp_kills"], t["xp_unknown_kills"]) == (5, 4, 185, 198, 4, 1),
          str({k: t[k] for k in ("kills", "looted", "gold", "xp", "xp_kills", "xp_unknown_kills")}))
    check("active time = the 3 visits (1800+900+900 s = 1 h); rates over in-visit events only (the 7000 s kill is outside)",
          t["active_s"] == 3600.0 and t["visits"] == 3
          and (t["kills_per_hour"], t["gold_per_hour"], t["xp_per_hour"]) == (4.0, 160.0, 173.0)
          and t["outside_visits"] == {"kills": 1, "gold": 25, "xp": 25}, str(t))
    check("per kill: gold 185/4 looted, xp 198/4 with known xp", (t["gold_per_kill"], t["xp_per_kill"]) == (46.25, 49.5))
    check("deaths, leaves, speech holds and their wait; other jobs' events excluded",
          t["deaths"] == {"pk": 0, "mob": 1, "other": 0, "total": 1} and t["leaves"] == 1
          and (t["speech_holds"], t["speech_wait_s"]) == (1, 12.5), str(t["deaths"]))
    v = a["visits"]
    check("visit rows: kills/gold/xp from the events in each visit; row counters (hits lost, casts) kept",
          [(r["n"], r["kills"], r["gold"], r["xp"], r["looted"]) for r in v] == [(1, 2, 50, 53, 2), (2, 1, 0, 0, 0),
                                                                                (3, 1, 110, 120, 1)]
          and (v[0]["hits_lost"], v[0]["casts"], v[0]["potions"]) == (40, 9, 1)
          and v[1]["events"] == {"death": 1, "leave": 1, "speech_hold": 1, "speech_clear": 1}
          and v[0]["xp_per_hour"] == 106.0, str([(r["kills"], r["gold"], r["xp"], r["events"]) for r in v]))
    check("monsters: kills and loot per name (old unnamed loot -> the latest unclaimed kill)",
          [(r["name"], r["kills"], r["looted"], r["gold"], r["xp"]) for r in a["monsters"]]
          == [("a mongbat", 4, 3, 75, 78), ("an orc", 1, 1, 110, 120)], str(a["monsters"]))
    roll = [(p["n"], p["xp_per_hour"], p["cum_xp_per_hour"], p["window_visits"]) for p in a["rolling"]]
    check("rolling 1 h window: 53/1800 s = 106 -> (53+0)/2700 s = 70.67 -> visit 3 alone 480; cumulative 106, 70.67, 173",
          roll == [(1, 106.0, 106.0, 1), (2, 70.67, 70.67, 2), (3, 480.0, 173.0, 1)], str(roll))
    days = {d["day"]: d for d in a["days"]}
    check("per day: 01-01 2 visits, 4 kills (one outside a visit), 75 gold; 01-02 the orc",
          list(days) == ["1970-01-01", "1970-01-02"]
          and (days["1970-01-01"]["visits"], days["1970-01-01"]["kills"], days["1970-01-01"]["gold"]) == (2, 4, 75)
          and (days["1970-01-02"]["kills"], days["1970-01-02"]["xp"]) == (1, 120), str(list(days)))
    check("timeline: no per-kill rows (kill/loot), the rest in time order",
          [e["kind"] for e in a["events"]] == ["speech_hold", "speech_clear", "death", "leave"],
          str([e["kind"] for e in a["events"]]))
    since = jobs.analytics(m, "hunt", 4000)
    check("since=4000: visits 2 and 3, the outside kill and the orc",
          (since["totals"]["visits"], since["totals"]["kills"], since["totals"]["xp"]) == (2, 3, 145),
          str(since["totals"]))
    upto = jobs.analytics(m, "hunt", 0, DAY)
    check("until=DAY: visits 1 and 2, the four day-1 kills, no orc",
          (upto["totals"]["visits"], upto["totals"]["kills"], [r["name"] for r in upto["monsters"]])
          == (2, 4, ["a mongbat"]), str(upto["totals"]))
    empty = jobs.analytics(None, "hunt", 0)
    check("no store: zeros and nulls, no division errors",
          empty["totals"]["kills"] == 0 and empty["totals"]["xp_per_hour"] is None
          and empty["totals"]["gold_per_kill"] is None and empty["visits"] == [] and empty["rolling"] == []
          and empty["monsters"] == [], str(empty["totals"]))
    check("loot_xp: xp wins; else gold piles (graphic as hex text or int); no items -> unknown",
          (jobs.loot_xp({"xp": 7, "items": [{"graphic": "0x0EED", "amount": 3}]}),
           jobs.loot_xp({"items": [{"graphic": "0x0eed", "amount": 3}, {"graphic": 0x0EED, "amount": 2}, {"graphic": "zz"}]}),
           jobs.loot_xp({"gold": 5})) == (7, 5, None))
    m.close()


def test_grove_route(m: memory.Memory, base: str):
    """GET /api/lumber/grove on the server over store `m` (the map files are read-only
    from the install dir; without them the route has no trees to show)."""
    import lumber_opt
    spots = lumber_opt.load_spots(m)
    s = spots["horseshoe_bay"]
    facet = int(s.get("facet") or 0)
    um = lumber_opt._umap(facet)
    if um is None:
        print("  [SKIP] /api/lumber/grove: no map files")
        return
    (cx, cy), r = s["area"]["center"], s["area"]["radius"]
    inside = [t for t in lumber_opt.area_trees(um, s["area"], s.get("trees") or [])]
    a, b, c = inside[:3]
    now = time.time()
    for t, outcome, at in ((a, "depleted", now - 60), (b, "not_tree", now - 60), (c, "unreachable", now - 10 * 86400)):
        m.harvest_record(facet, t["x"], t["y"], t["z"], int(t["graphic"], 16), outcome, t=at)
    g = get(base + "/api/lumber/grove?spot=horseshoe_bay")
    by = {(t["x"], t["y"]): t for t in g.get("trees", [])}
    st = [by.get((t["x"], t["y"]), {}).get("state") for t in (a, b, c)]
    check("GET /api/lumber/grove?spot=: the area, every runner tree inside it, harvest memory states "
          "(depleted a minute ago until the regrowth window ends, not_tree, an old unreachable is ready again)",
          g["spot"]["id"] == "horseshoe_bay" and g["spot"]["area"] == {"center": [cx, cy], "radius": r}
          and g["counts"]["trees"] == len(inside) and g["counts"]["ready"] == len(inside) - 2
          and st == ["depleted", "not_tree", "ready"]
          and by[(a["x"], a["y"])]["until"] == round(now - 60 + g["regrow_min"] * 60, 1)
          and all(t["inside"] for t in (by[(a["x"], a["y"])], by[(b["x"], b["y"])])),
          f"{g.get('counts')} {st}")
    out = [t for t in g["trees"] if not t["inside"]]
    check("trees past the area (up to the margin) come marked outside, none further",
          all(max(abs(t["x"] - cx), abs(t["y"] - cy)) <= r + g["margin"] for t in g["trees"])
          and all(max(abs(t["x"] - cx), abs(t["y"] - cy)) > r for t in out), str(len(out)))
    at = get(base + f"/api/lumber/grove?facet={facet}&x={cx}&y={cy}")
    far = get(base + f"/api/lumber/grove?facet={facet}&x=1&y=1")
    check("GET /api/lumber/grove by position: the spot whose area holds it, else spot null",
          at["spot"]["id"] == "horseshoe_bay" and far == {"spot": None, "store": True}, str(far))
    for q in ("", "x=12", "x=a&y=2"):
        code, _ = get_status(base + f"/api/lumber/grove?{q}")
        check(f"GET /api/lumber/grove?{q}: 400", code == 400, str(code))
    C = lumber_opt.CELL
    cell = [a["x"] // C, a["y"] // C]               # a forest of one cell: the tree `a` stands in it
    m.lumber_spot_put("horseshoe_bay", "active", {"area": {"center": [cell[0] * C + 3, cell[1] * C + 3], "radius": 4,
                                                           "cells": [cell]}}, "seed")
    f = get(base + "/api/lumber/grove?spot=horseshoe_bay")
    ins = [t for t in f.get("trees", []) if t["inside"]]
    check("a forest spot: its cells and their size come with the area; inside = in a cell; trees within the "
          "margin of the cells only",
          f["spot"]["area"].get("cells") == [cell] and f["spot"]["area"].get("cell") == C
          and ins and all([t["x"] // C, t["y"] // C] == cell for t in ins)
          and all(lumber_opt.area_dist(f["spot"]["area"], (t["x"], t["y"])) <= f["margin"] for t in f["trees"]),
          str(f.get("counts")))


def test_overseer_routes(logdir):
    print("== /api/jobs, /api/overseer, POST /api/chat ==")
    path = os.path.join(tempfile.mkdtemp(), "harness.db")
    m = memory.Memory(path)
    seed_jobs(m)
    m.chat_post("system", "overseer started")
    m.chat_post("overseer", "I will watch the lumber loop", t=100.0)
    m.chat_post("overseer", "trip 1 looks slow", kind="thought", t=101.0)
    m.chat_post("overseer", "ctl pause", kind="action", t=102.0)
    j1 = m.juncture("lumber", "captcha", "Captcha up; agent paused", "urgent", t=103.0)
    j2 = m.juncture("lumber", "trip_done", "Trip 1 done", "info", t=104.0)
    m.con.execute("INSERT OR REPLACE INTO meta VALUES('overseer_heartbeat', '1790742202.14')")
    m.con.commit()
    want = jobs.analytics(m, "lumber", 0, utc_offset_s=0)
    d = viz_feed.ReplayDriver(TAG, logdir)
    port = OVERSEER_PORT
    base = f"http://127.0.0.1:{port}"
    srv = serve(d, port, path)
    try:
        got = get(base + "/api/jobs?job=lumber&tz=0")
        check("GET /api/jobs == jobs.analytics(store) + store flag, no plan (its own route)",
              got.pop("store") is True and "plan" not in got
              and json.dumps(got, sort_keys=True) == json.dumps(want, sort_keys=True))
        pl = get(base + "/api/jobs/plan")
        plan = pl["plan"]
        check("GET /api/jobs/plan: the plan at the server's clock (the seeded trips name no character, so no "
              "home: ok false with the reason, the spots still ranked)",
              pl["store"] is True and isinstance(plan, dict)
              and {r["id"] for r in plan["spots"]} >= {"horseshoe_bay", "terran_wilds"}
              and "shelter_island" not in {r["id"] for r in plan["spots"]} and plan["ok"] is False
              and plan["error"].startswith("no home in harness/data/homes.json for"),
              str(plan)[:300])
        entered, release, real_plan = threading.Event(), threading.Event(), viz_server.jobs_mod.lumber_plan

        def slow_plan(mem, t):
            entered.set()
            release.wait(10)
            return real_plan(mem, t)
        viz_server.jobs_mod.lumber_plan, srv.overseer.plan_cache = slow_plan, None
        try:
            pending = threading.Thread(target=get, args=(base + "/api/jobs/plan",), daemon=True)
            pending.start()
            entered.wait(5)
            t0 = time.monotonic()
            side = get(base + "/api/jobs?job=lumber&since=1&tz=0"), get(base + "/api/overseer")
            waited = time.monotonic() - t0
        finally:
            release.set()
            viz_server.jobs_mod.lumber_plan = real_plan
        pending.join(10)
        check("while the plan computes, /api/jobs and /api/overseer answer at once (the plan has its own lock)",
              entered.is_set() and waited < 2 and side[0]["totals"]["trips"] == 3 and side[1]["store"] is True,
              f"waited {waited:.2f} s")
        code, _ = get_status(base + "/api/jobs?tz=abc")
        check("GET /api/jobs bad tz: 400", code == 400, str(code))
        code, _ = get_status(base + "/api/jobs?since=soon")
        check("GET /api/jobs bad since: 400", code == 400, str(code))
        ranged = get(base + "/api/jobs?job=lumber&since=4000&until=86400&tz=0")
        check("GET /api/jobs since+until == jobs.analytics over [since, until)",
              (ranged["since"], ranged["until"], ranged["totals"]["trips"]) == (4000, 86400, 1),
              str((ranged["since"], ranged["until"], ranged["totals"]["trips"])))
        for q in ("since=5000&until=5000", "since=5000&until=4000", "until=nan", "until=later"):
            code, _ = get_status(base + f"/api/jobs?{q}")
            check(f"GET /api/jobs {q}: 400", code == 400, str(code))
        ov = get(base + "/api/overseer")
        check("GET /api/overseer: all chat rows oldest first, roles/kinds kept",
              [(c["role"], c["kind"]) for c in ov["chat"]] == [("system", "message"), ("overseer", "message"),
                                                               ("overseer", "thought"), ("overseer", "action")],
              str(ov["chat"]))
        check("junctures with severity, 2 open, heartbeat as a float",
              [(j["id"], j["severity"]) for j in ov["junctures"]] == [(j1, "urgent"), (j2, "info")]
              and ov["open"] == 2 and ov["open_ids"] == [j1, j2] and ov["heartbeat"] == 1790742202.14
              and ov["store"] is True, str({k: ov[k] for k in ("open", "open_ids", "heartbeat")}))
        m.juncture_ack(j1)
        last_chat = ov["chat"][-1]["id"]
        code, resp = post(base + "/api/chat", {"text": "  please head to the forest  "})
        check("POST /api/chat: 200 with the row id", code == 200 and resp["ok"] is True
              and resp["id"] == last_chat + 1, f"{code} {resp}")
        ov2 = get(base + f"/api/overseer?after_chat={last_chat}&after_juncture={j2}")
        check("cursors: only the new user row (trimmed), no old junctures; ack reflected in open",
              [(c["role"], c["kind"], c["text"]) for c in ov2["chat"]] == [("user", "message", "please head to the forest")]
              and ov2["junctures"] == [] and ov2["open"] == 1 and ov2["open_ids"] == [j2], str(ov2))
        check("the overseer reads it through Memory.chat(role='user')",
              [c["text"] for c in m.chat(role="user")] == ["please head to the forest"])
        for name, body in [("empty", {"text": ""}), ("whitespace", {"text": " \n\t "}), ("missing", {}),
                           ("not a string", {"text": 42}), ("2001 chars", {"text": "x" * 2001})]:
            code, resp = post(base + "/api/chat", body)
            check(f"POST /api/chat {name}: 400", code == 400 and resp["ok"] is False, f"{code} {resp}")
        code, resp = post(base + "/api/chat", {"text": "y" * 2000})
        check("POST /api/chat exactly 2000 chars: 200", code == 200, str(code))
        check("rejected posts stored nothing", len(m.chat(role="user")) == 2, str(len(m.chat(role="user"))))
        code, _ = get_status(base + "/api/overseer?after_chat=x")
        check("GET /api/overseer bad cursor: 400", code == 400, str(code))
        test_grove_route(m, base)
    finally:
        srv.shutdown()
        srv.server_close()
        d.stop()

    # newest-200 window on first load
    for i in range(205):
        m.chat_post("overseer", f"n{i}", t=200.0 + i)
    top = m.con.execute("SELECT MAX(id) FROM chat").fetchone()[0]
    m.close()
    port = OVERSEER_PORT2
    base = f"http://127.0.0.1:{port}"
    srv = serve(viz_feed.ReplayDriver(TAG, logdir), port, path)
    try:
        ov = get(base + "/api/overseer")
        ids = [c["id"] for c in ov["chat"]]
        check("after_chat=0 returns the newest 200 rows, oldest first", ids == list(range(top - 199, top + 1)),
              f"{ids[:2]}..{ids[-2:]}")
    finally:
        srv.shutdown()
        srv.server_close()

    # no store yet: GETs answer empty and create nothing; the first chat creates it
    missing = os.path.join(tempfile.mkdtemp(), "harness.db")
    port = OVERSEER_PORT3
    base = f"http://127.0.0.1:{port}"
    srv = serve(viz_feed.ReplayDriver(TAG, logdir), port, missing)
    try:
        jb = get(base + "/api/jobs?job=lumber")
        ov = get(base + "/api/overseer")
        check("no store: /api/jobs empty with store=false", jb["store"] is False and jb["trips"] == []
              and jb["totals"]["trips"] == 0, str(jb["totals"]["trips"]))
        check("no store: /api/jobs/plan null with store=false", get(base + "/api/jobs/plan") == {"plan": None, "store": False})
        hb = get(base + "/api/jobs?job=hunt")
        check("no store: /api/jobs?job=hunt is the hunt shape, empty, store=false",
              hb["store"] is False and hb["job"] == "hunt" and hb["visits"] == [] and hb["totals"]["kills"] == 0,
              str(hb.get("totals")))
        check("no store: /api/overseer empty, heartbeat null", ov["store"] is False and ov["chat"] == []
              and ov["open"] == 0 and ov["heartbeat"] is None)
        cm = get(base + "/api/captcha")
        check("no store: captcha mode defaults to human", cm == {"mode": "human", "store": False}, str(cm))
        check("GETs did not create the store", not os.path.exists(missing))
        code, resp = post(base + "/api/chat", {"text": "hello?"})
        ov = get(base + "/api/overseer")
        check("first chat creates the store and is served back", code == 200 and os.path.exists(missing)
              and [c["text"] for c in ov["chat"]] == ["hello?"], f"{code} {resp}")
        cm = get(base + "/api/captcha")
        check("store without a captcha_mode row: human", cm == {"mode": "human", "store": True}, str(cm))
        for name, body in [("unknown", {"mode": "robot"}), ("missing", {}), ("not a string", {"mode": ["auto"]})]:
            code, resp = post(base + "/api/captcha", body)
            check(f"POST /api/captcha {name}: 400", code == 400 and resp["ok"] is False, f"{code} {resp}")
        code, resp = post(base + "/api/captcha", {"mode": "auto"})
        runner = memory.Memory(missing)
        seen = runner.captcha_mode()
        check("POST /api/captcha auto: 200; the runner's Memory and GET both read auto",
              code == 200 and resp == {"ok": True, "mode": "auto"} and seen == "auto"
              and get(base + "/api/captcha")["mode"] == "auto", f"{code} {resp} {seen}")
        code, resp = post(base + "/api/captcha", {"mode": "human"})
        seen = runner.captcha_mode()
        runner.close()
        check("POST /api/captcha human: back to human", code == 200 and seen == "human", f"{code} {resp} {seen}")
    finally:
        srv.shutdown()
        srv.server_close()


def parse_frame(raw: str) -> dict:
    """One SSE frame (without its blank-line terminator) as {id, event, data}."""
    f = {}
    for line in raw.split("\n"):
        if line.startswith(":") or not line:
            continue
        k, _, v = line.partition(": ")
        f[k] = v
    return f


def sse_frames(text: str) -> list:
    """The frames (with an event) of an SSE text."""
    return [f for f in map(parse_frame, text.split("\n\n")) if "event" in f]


class SSE:
    """Minimal SSE reader: frames as dicts {id, event, data}."""

    def __init__(self, url, last_id=None):
        self.sock = socket.create_connection(("127.0.0.1", int(url.split(":")[2].split("/")[0])), timeout=5)
        path = "/" + url.split("/", 3)[3]
        hdr = f"GET {path} HTTP/1.1\r\nHost: x\r\nAccept: text/event-stream\r\n"
        if last_id is not None:
            hdr += f"Last-Event-ID: {last_id}\r\n"
        self.sock.sendall((hdr + "\r\n").encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            self.buf += self.sock.recv(65536)
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        self.headers = head.decode()

    def frames(self, until, timeout=5.0):
        """Read frames until until(frames) is true or timeout."""
        out = []
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            while b"\n\n" in self.buf:
                raw, self.buf = self.buf.split(b"\n\n", 1)
                f = parse_frame(raw.decode())
                if "event" in f:
                    out.append(f)
            if until(out):
                return out
            self.sock.settimeout(max(0.01, end - time.monotonic()))
            try:
                d = self.sock.recv(65536)
            except socket.timeout:
                break
            if not d:
                break
            self.buf += d
        return out

    def close(self):
        self.sock.close()


def has_state(fr):
    return any(f["event"] == "state" for f in fr)


def batch_envs(fr) -> list:
    """The envelopes of every `world_events` frame, in arrival order."""
    return [e for f in fr if f["event"] == "world_events" for e in json.loads(f["data"])]


# --------------------------------------------------------------------- replay

def test_replay_parity(logdir):
    print(f"== replay parity: {TAG} (pinned at {TAG_COMMIT}) ==")
    d = viz_feed.ReplayDriver(TAG, logdir)
    d.run_to_end()
    st = d.tap.state()
    check("order is exact (every jsonl row matched its packet)", d.order == "exact" and d.order_note is None,
          f"{d.order} {d.order_note}")
    check("timeline fully applied", d.position == len(d.items), f"{d.position}/{len(d.items)}")
    pos = st["movement"]["pos"]
    check("final movement.pos == [1963, 2597, 0, facing]", pos is not None and pos[:3] == [1963, 2597, 0], str(pos))
    ws = st["world"]["self"]
    check("world-model self == movement truth after the errand (confirm-driven, turns don't move)",
          pos is not None and [ws["x"], ws["y"], ws["direction"]] == [pos[0], pos[1], pos[3]],
          f"world {(ws['x'], ws['y'], ws['direction'])} vs truth {pos}")
    items = st["world"]["items"]
    me = st["world"]["self"]["serial"]
    bank = [int(k, 16) for k, v in items.items() if v.get("layer") == 0x1D and v.get("container") == me]
    opens = [e["data"]["serial"] for e in st["events"]
             if e["origin"] == "world" and e["data"]["ev"] == "container_open"]
    check("container_open on the bank box (self item, layer 0x1D)", len(bank) == 1 and bank[0] in opens,
          f"bank={[hex(b) for b in bank]} opens={[hex(o) for o in opens]}")
    item_ts = {it[0] for it in d.items}
    check("world event t = the time its packet was processed (a jsonl row t), not drain time",
          all(e["t"] in item_ts for e in st["events"] if e["origin"] == "world"))
    pev = collections.Counter(e["data"]["ev"] for e in st["events"] if e["origin"] == "proxy")
    check("proxy events: 36 step, 2 reanchor_client", pev["step"] == 36 and pev["reanchor_client"] == 2, str(dict(pev)))
    c2s = collections.Counter((e["data"]["src"], e["data"]["id"]) for e in st["events"]
                              if e["origin"] == "proxy" and e["data"]["ev"] == "c2s")
    check("c2s agent events: 54 walks + 1 speech, nothing else",
          c2s == {("agent", "0x02"): 54, ("agent", "0xAD"): 1}, str(dict(c2s)))
    check("54 agent confirms hidden", pev["s2c_confirm_hidden"] == 54, str(pev["s2c_confirm_hidden"]))
    check("recorded timer decisions all reproduced", d.timer_divergences == 0, str(d.timer_divergences))
    check("snapshot labels keep the banker's click label (no event-ring dependency)",
          st["world"]["labels"].get("0x000001EA") == "Len the banker", str(st["world"].get("labels")))
    tc = {(s, p): n for s, p, n in st["traffic"]["c2s"]}
    check("cumulative traffic: 54 agent walks + 1 agent speech, 54 hidden confirms, 2 re-anchors",
          tc == {("agent", "0x02"): 54, ("agent", "0xAD"): 1}
          and st["traffic"]["proxy_events"].get("s2c_confirm_hidden") == 54
          and st["traffic"]["proxy_events"].get("reanchor_client") == 2, str(st["traffic"]))
    check("envelopes: seq 0..next-1, numeric t, known origin",
          [e["seq"] for e in st["events"]] == list(range(st["next"]))
          and all(isinstance(e["t"], float) and e["origin"] in ("world", "proxy") for e in st["events"]))

    srv = serve(d, SERVER_PORT)
    try:
        d.pump()
        got = get(f"http://127.0.0.1:{SERVER_PORT}/api/state")
        want = json.loads(json.dumps({"ok": True, **d.tap.state(), "viz": d.viz()}))
        check("GET /api/state == offline SessionTap.state() + viz block", got == want,
              "" if got == want else str(sorted(k for k in set(got) | set(want) if got.get(k) != want.get(k))))
        check("viz block: replay, session tag, exact, playback at the end",
              got["viz"] == {"mode": "replay", "session": TAG, "order": "exact", "connected": True,
                             "playback": {"playing": False, "rate": 1.0, "position": len(d.items),
                                          "total": len(d.items)}}, str(got["viz"]))
        h = get(f"http://127.0.0.1:{SERVER_PORT}/api/health")
        check("health: mode/order/diagnostics", h["mode"] == "replay" and h["order"] == "exact"
              and h["diagnostics"]["packet_counts"][0][2] > 0, str({k: h[k] for k in ("mode", "order", "ring")}))
        wm = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{SERVER_PORT}/api/walkmem").read())
        check("/api/walkmem serves the store's projection (edge + deny)",
              wm["edges"] == [[10, 10, 11, 10]] and wm["blocked"] == [[11, 10, 2]], str(wm))
        # the Corpse Creek house that denied 12 agent steps (live 20261002_153718), from the install's multi.mul
        mf = get(f"http://127.0.0.1:{SERVER_PORT}/api/multi/0x154")
        check("/api/multi/0x154: ground-storey wall corner and floor inside, no piece past the footprint",
              [-2, -3, "wall"] in mf["tiles"] and [-1, -2, "floor"] in mf["tiles"]
              and not any(t[:2] == [2, -3] for t in mf["tiles"]), str(mf["tiles"][:6]))
        code, resp = get_status(f"http://127.0.0.1:{SERVER_PORT}/api/multi/99999")
        check("/api/multi of an id multi.mul doesn't have: 404", code == 404, f"{code} {resp}")
        code, _ = post(f"http://127.0.0.1:{SERVER_PORT}/api/playback", {"action": "rate", "rate": 0})
        check("POST /api/playback rejects rate 0", code == 400, str(code))
        code, resp = post(f"http://127.0.0.1:{SERVER_PORT}/api/gate", {"action": "pause"})
        check("POST /api/gate in replay: 409 no gate", code == 409 and resp["error"] == "no gate in replay",
              f"{code} {resp}")
        code, resp = post(f"http://127.0.0.1:{SERVER_PORT}/api/gate", {"action": "rearm"})
        check("POST /api/gate rearm: 400 (CLI-only)", code == 400, f"{code} {resp}")
    finally:
        srv.shutdown()
        srv.server_close()


def test_order_fallback():
    print(f"== order fallback: {OLD_TAG} ==")
    d = viz_feed.ReplayDriver(OLD_TAG, os.path.join(ROOT, "logs"))
    check("pre-fix capture replays in approximate order", d.order == "approx", f"{d.order} {d.order_note}")
    check("fallback reason recorded", bool(d.order_note), str(d.order_note))
    d.run_to_end()
    st = d.tap.state()
    check("approx replay still builds the world (self known)", st["world"].get("self", {}).get("serial") is not None)
    check("approx viz block says approx", d.viz()["order"] == "approx")


def test_sse(logdir):
    print("== SSE framing, resume, coalescing ==")
    d = viz_feed.ReplayDriver(TAG, logdir)
    srv = serve(d, SSE_PORT)
    url = f"http://127.0.0.1:{SSE_PORT}/api/events"
    try:
        for _ in range(300):
            d.step()
        d.pump()
        s = SSE(url)
        check("SSE content type", "text/event-stream" in s.headers.lower(), s.headers.splitlines()[0])
        fr = s.frames(has_state)
        s.close()
        evs = batch_envs(fr)
        ids = [e["seq"] for e in evs]
        check("fresh connect: the whole ring as one world_events frame, then one state",
              ids == list(range(d.cursor)) and [f["event"] for f in fr] == ["world_events", "state"],
              f"{len(ids)} events, next {d.cursor}, frames {[f['event'] for f in fr]}")
        check("batch frame id == its last envelope's seq; envelopes verbatim",
              int(fr[0]["id"]) == ids[-1]
              and all(e == json.loads(json.dumps(d.tap.events[e["seq"]])) for e in evs))
        state = json.loads(fr[-1]["data"])
        check("state frame = response without events, with viz", "events" not in state and state["ok"]
              and state["viz"]["playback"]["position"] == 300 and "movement" in state and "world" in state)
        last = int(fr[0]["id"])

        for _ in range(400):
            d.step()
        d.pump()
        s = SSE(url, last_id=last)
        fr2 = s.frames(has_state)
        s.close()
        ids2 = [e["seq"] for e in batch_envs(fr2)]
        check("Last-Event-ID resume: exactly the missed suffix, no duplicates",
              ids2 == list(range(last + 1, d.cursor)) and ids2, f"{ids2[:3]}..{ids2[-3:]} next {d.cursor}")
        check("resume + first stream = gapless seqs", ids + ids2 == list(range(d.cursor)))
        s = SSE(url + f"?since={d.cursor - 5}")
        fr3 = s.frames(has_state)
        s.close()
        check("?since=N resume", [e["seq"] for e in batch_envs(fr3)] == list(range(d.cursor - 5, d.cursor)))

        # coalescing: pump thread running; a 500-packet burst -> one state frame
        d.run()
        s = SSE(url)
        s.frames(has_state)            # initial ring + state
        time.sleep(0.6)                # settle: unchanged state is not re-sent
        quiet = s.frames(lambda f: False, timeout=0.6)
        before = d.cursor
        with d.sim:
            for _ in range(500):
                d.step()
        fr4 = s.frames(lambda f: False, timeout=1.2)
        s.close()
        ids4 = [e["seq"] for e in batch_envs(fr4)]
        check("idle: no frames while nothing changes", quiet == [], str(quiet[:2]))
        check("burst coalesced into one state frame", sum(f["event"] == "state" for f in fr4) == 1,
              str([f["event"] for f in fr4 if f["event"] == "state"]))
        check("burst events streamed in order", ids4 == list(range(before, d.cursor)) and ids4,
              f"{len(ids4)} of {d.cursor - before}")
    finally:
        d.stop()
        srv.shutdown()
        srv.server_close()


# ------------------------------------------------------------ ping-pong burst

BURST_PUMPS = 624          # 156 s at 4 Hz: session 20261005_093927's first ping-pong window
BURST_READER_BPS = 4e6     # a slow SSE reader (a LAN device), bytes/s
KEEP_EVS = ("speech_heard", "agent_intent", "cliloc", "gump_open", "gump_response")


class HandFeed(viz_feed.Feed):
    """A live-shaped feed the test publishes by hand, one state-port response per pump."""

    mode = "live"

    def viz(self) -> dict:
        return {"mode": "live", "session": None, "order": None, "playback": None, "connected": True}


def burst_responses() -> list:
    """State-port responses of a ping-pong burst the size of session 20261005_093927's
    window t 1791235352-508 (the runner crossing the view edge for 156 s): ~8500
    item_seen (S2C 0xF3 re-sends of 475 objects), ~1900 other entity churn, ~780 each of
    walk / c2s / walk_confirm / s2c_confirm_hidden / reanchor_client, ~550 steps, plus
    speech, intents, clilocs and a gump. Every state differs (the position bounces)."""
    items = {f"0x{0x40000000 + i:08X}": {"graphic": 0x0CD0 + i % 40, "hue": 0, "amount": 1, "x": 1900 + i % 40,
                                         "y": 2560 + i // 40, "z": 0, "name": f"item {i}"} for i in range(475)}
    last_seen = {f"0x{0x00010000 + i:08X}": {"name": f"a mongbat {i}", "x": 1900 + i % 30, "y": 2600, "z": 0,
                                             "t": 1000.0 + i, "facet": 0, "why": "range"} for i in range(200)}
    out, seq, walk = [], 0, 0
    for i in range(BURST_PUMPS):
        t = round(1000.0 + i / 4, 3)
        x = 1925 + (i % 40 if (i // 40) % 2 == 0 else 39 - i % 40)
        mob = 0x00010000 + i % 200
        data = [("world", {"ev": "item_seen", "serial": 0x40000000 + (i * 14 + k) % 475, "graphic": 0x0CD0,
                           "container": None}) for k in range(14 if i % 3 else 13)]
        data += [("world", {"ev": "names", "count": 1, "entries": [{"serial": mob, "name": "a mongbat"}]}),
                 ("world", {"ev": "sound", "sound": 0x23A, "x": x, "y": 2580}),
                 ("world", {"ev": "query", "serial": mob, "kind": 0x34})]
        for _ in range(2 if i % 4 == 0 else 1):
            data += [("world", {"ev": "walk", "dir": 2, "seq": walk % 256}),
                     ("proxy", {"ev": "c2s", "src": "agent", "id": "0x02"}),
                     ("world", {"ev": "walk_confirm", "seq": walk % 256}),
                     ("proxy", {"ev": "s2c_confirm_hidden", "seq": walk % 256}),
                     ("proxy", {"ev": "reanchor_client", "on": "confirm"})]
            walk += 1
        if i % 9:
            data.append(("proxy", {"ev": "step", "from": [x - 1, 2580], "to": [x, 2580], "z": 0}))
        if i % 2 == 0:
            data.append(("world", {"ev": "speech_heard", "serial": mob, "type": 0, "text": f"line {i}"}))
        if i % 6 == 0:
            data.append(("proxy", {"ev": "agent_intent", "intent": {"text": f"Heading to tree {i}", "kind": "to_tree"}}))
        if i % 25 == 0:
            data.append(("world", {"ev": "cliloc", "cliloc": 500495, "args": ""}))
        if i == 300:
            data.append(("world", {"ev": "gump_open", "serial": 0x0604, "gump_id": 0x165FF74B, "lines": ["Captcha"]}))
        if i == 320:
            data.append(("world", {"ev": "gump_response", "serial": 0x0604, "gump_id": 0x165FF74B, "button_id": 1}))
        envs = []
        for origin, d in data:
            envs.append({"seq": seq, "t": t, "origin": origin, "data": d})
            seq += 1
        out.append({"ok": True, "movement": {"pos": [x, 2580, 0, 2], "self_serial": SELF, "inflight": 0},
                    "world": {"self": {"serial": f"0x{SELF:08X}", "x": x, "y": 2580, "z": 0},
                              "items": items, "last_seen": last_seen},
                    "events": envs, "next": seq, "world_errors": 0,
                    "intent": {"text": f"Heading to tree {i - i % 6}", "kind": "to_tree"}})
    return out


class ThrottledSSE:
    """SSE reader thread consuming at most `bps` bytes/s with a small receive buffer."""

    def __init__(self, port: int, bps: float):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024)
        self.sock.connect(("127.0.0.1", port))
        self.sock.sendall(b"GET /api/events HTTP/1.1\r\nHost: x\r\n\r\n")
        self.bps, self.bytes, self.states, self.envs = bps, 0, 0, []
        self.last_state = self.last_state_at = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        buf, t0 = b"", time.monotonic()
        try:
            while True:
                d = self.sock.recv(65536)
                if not d:
                    return
                self.bytes += len(d)
                buf += d
                while b"\n\n" in buf:
                    raw, buf = buf.split(b"\n\n", 1)
                    f = parse_frame(raw.decode())
                    if f.get("event") == "state":
                        self.states += 1
                        self.last_state, self.last_state_at = f["data"], time.monotonic()
                    elif f.get("event") == "world_events":
                        self.envs += json.loads(f["data"])
                ahead = self.bytes / self.bps - (time.monotonic() - t0)
                if ahead > 0:
                    time.sleep(ahead)
        except OSError:
            return

    def close(self):
        self.sock.close()


def test_burst():
    print("== ping-pong burst (session 20261005_093927 size): the newest state within 1 s ==")
    resps = burst_responses()
    n = resps[-1]["next"]
    counts = collections.Counter(e["data"]["ev"] for r in resps for e in r["events"])
    keep = [e["seq"] for r in resps for e in r["events"] if e["data"]["ev"] in KEEP_EVS]
    feed = HandFeed()
    want = json.loads(json.dumps({**{k: v for k, v in resps[-1].items() if k != "events"}, "viz": feed.viz()}))
    print(f"  {BURST_PUMPS} pumps, {n} events: {counts['item_seen']} item_seen, {counts['walk']} walks, "
          f"{counts['step']} steps, {len(keep)} speech/intent/cliloc/gump")

    # a connection that stops reading for the whole burst
    sub = feed.subscribe(None)
    deepest = 0
    for r in resps:
        feed._publish(dict(r))
        deepest = max(deepest, len(sub))
    t0 = time.perf_counter()
    fr = sse_frames(sub.take(0))
    took = time.perf_counter() - t0
    feed.unsubscribe(sub)
    seqs = [e["seq"] for e in batch_envs(fr)]
    check("stalled reader: its mailbox stays bounded (no frame backlog)",
          deepest <= viz_feed.SUB_EVENTS_MAX + 1, f"deepest {deepest}")
    check("after the burst: one events batch, then the newest state = the last proxy response, in <1 s",
          [f["event"] for f in fr] == ["world_events", "state"] and json.loads(fr[-1]["data"]) == want
          and took < 1.0, f"{[f['event'] for f in fr]} {took:.3f}s")
    check("events in seq order up to the newest; every speech/intent/cliloc/gump kept, in order",
          seqs == sorted(set(seqs)) and seqs[-1] == n - 1 and [s for s in seqs if s in set(keep)] == keep,
          f"{len(seqs)} of {n} kept")
    check("only superseded or old churn was dropped (counted in health)",
          feed.sse_dropped == n - len(seqs) > 0 and feed.health()["sse_dropped"] == feed.sse_dropped,
          str(feed.sse_dropped))

    # end to end: a reader slower than the burst over HTTP
    feed = HandFeed()
    srv = serve(feed, BURST_PORT)
    rd = ThrottledSSE(BURST_PORT, BURST_READER_BPS)
    try:
        end = time.monotonic() + 5
        while not feed.subscribers and time.monotonic() < end:
            time.sleep(0.01)
        made = 0
        for r in resps:
            feed._publish(dict(r))
            made += len(feed.state_json)
        t_end = time.monotonic()
        final = feed.state_json
        end = t_end + 10
        while rd.last_state != final and time.monotonic() < end:
            time.sleep(0.01)
        lag = rd.last_state_at - t_end if rd.last_state == final else None
        check(f"reader at {BURST_READER_BPS / 1e6:g} MB/s: the newest state arrives <1 s after the burst",
              lag is not None and lag < 1.0,
              f"{'never' if lag is None else f'{lag:.2f}s'}; read {rd.bytes / 1e6:.1f} MB of {made / 1e6:.1f} MB "
              f"of states published, {rd.states} of {BURST_PUMPS} states")
        check("... by skipping stale states, not queueing them", rd.states < BURST_PUMPS / 2, str(rd.states))
        time.sleep(0.2)
        got = [e["seq"] for e in rd.envs]
        check("... events in seq order; every speech/intent/cliloc/gump arrived",
              got == sorted(set(got)) and got[-1] == n - 1 and [s for s in got if s in set(keep)] == keep,
              f"{len(got)} of {n}")
        st = get(f"http://127.0.0.1:{BURST_PORT}/api/state")
        check("/api/state = the newest response + viz, ring up to the newest event",
              {k: v for k, v in st.items() if k != "events"} == want and st["events"][-1]["seq"] == n - 1)
    finally:
        rd.close()
        srv.shutdown()
        srv.server_close()


# ----------------------------------------------------------------------- live

def xor(bs, k):
    return bytes(b ^ k for b in bs)


def login_pkt(serial, x, y, z, direction):
    body = (serial.to_bytes(4, "big") + bytes(4) + (0x190).to_bytes(4, "big")
            + x.to_bytes(4, "big") + y.to_bytes(4, "big") + z.to_bytes(4, "big", signed=True)
            + bytes([direction]))
    return b"\x1b" + body + bytes(42 - len(body))


def seed_pkt(token):
    return bytes.fromhex("bf001d0001") + token.to_bytes(4, "big") + bytes(20)


def fake_upstream(ready: threading.Event, stop: threading.Event):
    """Fake game server: prelude + login + seed, confirms every 7-byte walk."""
    ls = socket.create_server(("127.0.0.1", UPSTREAM_PORT))
    ls.settimeout(10)
    ready.set()
    try:
        conn, _ = ls.accept()
    except socket.timeout:
        return
    conn.settimeout(0.2)
    buf = b""
    while len(buf) < 5:
        buf += conn.recv(5 - len(buf))
    conn.sendall(PRELUDE + encode_packet(login_pkt(SELF, 100, 200, 5, 0x80), S2C_KEY)
                 + encode_packet(seed_pkt(8), S2C_KEY))
    buf = b""
    while not stop.is_set():
        try:
            d = conn.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            break
        if not d:
            break
        buf += xor(d, C2S_KEY)
        while len(buf) >= 7 and buf[0] == 0x02:
            conn.sendall(encode_packet(bytes([0x22, buf[2], 0x01]), S2C_KEY))
            buf = buf[7:]
    conn.close()
    ls.close()


def test_live():
    print("== live: viz_server --live against a proxy subprocess ==")
    logdir = tempfile.mkdtemp(prefix="viz_live_")
    live_db = os.path.join(tempfile.mkdtemp(), "harness.db")
    lm = memory.Memory(live_db)
    seed_jobs(lm)
    lm.close()
    viz = subprocess.Popen([PY, "-u", os.path.join(HERE, "viz_server.py"), "--live", "--state-port", str(STATE_PORT),
                            "--port", str(VIZ_PORT), "--memory-db", live_db],
                           stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    proxy = None
    ctl_peer = None
    stop = threading.Event()
    base = f"http://127.0.0.1:{VIZ_PORT}"
    try:
        for _ in range(50):
            try:
                st = get(base + "/api/state")
                break
            except OSError:
                time.sleep(0.1)
        time.sleep(0.5)
        st = get(base + "/api/state")
        check("proxy down: served, connected=false", st["viz"]["connected"] is False and st["ok"] is False
              and st["viz"]["mode"] == "live", str(st.get("viz")))
        code, resp = post(base + "/api/gate", {"action": "pause"})
        check("proxy down: POST /api/gate 502", code == 502 and resp["ok"] is False, f"{code} {resp}")
        code, resp = post(base + "/api/chat", {"text": "live hello"})
        ov = get(base + "/api/overseer")
        jb = get(base + "/api/jobs?job=lumber&tz=0")
        check("live, proxy down: chat post + overseer + jobs served from the memory store",
              code == 200 and [c["text"] for c in ov["chat"]] == ["live hello"] and jb["totals"]["trips"] == 3,
              f"{code} {resp} {jb['totals']['trips']}")

        ready = threading.Event()
        threading.Thread(target=fake_upstream, args=(ready, stop), daemon=True).start()
        ready.wait(5)
        proxy = subprocess.Popen(
            [PY, "-u", os.path.join(HERE, "proxy.py"), "--listen-port", str(PROXY_PORT),
             "--upstream-host", "127.0.0.1", "--upstream-port", str(UPSTREAM_PORT),
             "--control-port", str(CONTROL_PORT), "--state-port", str(STATE_PORT), "--logdir", logdir],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        end = time.monotonic() + 30
        while True:   # connect once the proxy subprocess listens (replaces a fixed startup sleep)
            try:
                client = socket.create_connection(("127.0.0.1", PROXY_PORT), timeout=5)
                break
            except ConnectionRefusedError:
                if time.monotonic() > end:
                    raise
                time.sleep(0.05)
        client.sendall(bytes.fromhex("ef0000000c"))
        time.sleep(0.3)
        client.sendall(xor(bytes([0x02, 0x80, 5, 0, 0, 0, 0]), C2S_KEY))   # client walks N
        st = None
        end = time.monotonic() + 6
        while time.monotonic() < end:
            st = get(base + "/api/state")
            if st.get("ok") and st["movement"]["pos"] == [100, 199, 5, 0]:
                break
            time.sleep(0.2)
        check("poller reconnected: connected=true, ok", st["viz"]["connected"] is True and st["ok"] is True,
              str(st.get("viz")))
        check("live movement read through the viz", st["movement"]["pos"] == [100, 199, 5, 0]
              and st["movement"]["self_serial"] == SELF, str(st.get("movement")))
        steps = [e["data"] for e in st["events"] if e["origin"] == "proxy" and e["data"]["ev"] == "step"]
        check("live proxy step envelope", steps == [{"ev": "step", "from": [100, 200], "to": [100, 199], "z": 5}],
              str(steps))
        check("live world events wrapped (client walk seen)",
              any(e["origin"] == "world" and e["data"]["ev"] == "walk" for e in st["events"]))
        s = SSE(base + "/api/events")
        fr = s.frames(has_state)
        s.close()
        check("live SSE: gapless ring + state", has_state(fr)
              and [e["seq"] for e in batch_envs(fr)] == list(range(st["next"])))

        # agent gate through the viz (the proxy's budget file lives in the private logdir)
        check("state responses carry the gate", (st.get("gate") or {}).get("state") == "running", str(st.get("gate")))
        g = get(base + "/api/gate")
        check("GET /api/gate: running", g.get("ok") is True and g["gate"]["state"] == "running", str(g))
        code, resp = post(base + "/api/gate", {"action": "pause"})
        check("POST /api/gate pause: 200, paused", code == 200 and resp["ok"] is True
              and resp["gate"]["state"] == "paused" and resp["gate"]["paused"] is True, f"{code} {resp}")
        g = state_port({"op": "gate"})
        check("proxy's own gate reports paused", g["gate"]["state"] == "paused" and g["gate"]["blocked"] is True,
              str(g))
        check("viz state picks up the pause", wait_state(base, lambda s: (s.get("gate") or {}).get("paused") is True))
        ctl = socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=5)
        ctl_peer = ctl.getsockname()
        click = bytes([0x09]) + SELF.to_bytes(4, "big")
        ctl.sendall(len(click).to_bytes(2, "big") + click)
        reply = recv_exact(ctl, int.from_bytes(recv_exact(ctl, 2), "big")).decode()
        ctl.close()
        check("paused: control-port injection gets ERR ... paused", reply.startswith("ERR") and "paused" in reply,
              reply)
        code, resp = post(base + "/api/gate", {"action": "resume"})
        check("POST /api/gate resume: 200, running", code == 200 and resp["gate"]["state"] == "running"
              and resp["gate"]["paused"] is False, f"{code} {resp}")
        code, resp = post(base + "/api/gate", {"action": "rearm"})
        check("POST /api/gate rearm: 400, not forwarded", code == 400 and resp["ok"] is False
              and state_port({"op": "gate"})["gate"]["state"] == "running", f"{code} {resp}")
        code, resp = post(base + "/api/gate", {"action": "kill"})
        check("POST /api/gate kill: 200, killed", code == 200 and resp["gate"]["state"] == "killed", f"{code} {resp}")
        code, resp = post(base + "/api/gate", {"action": "resume"})
        check("resume while killed: 409 with the proxy's error + gate", code == 409 and resp["ok"] is False
              and resp.get("error") and resp["gate"]["killed"] is True, f"{code} {resp}")
        client.close()
        time.sleep(0.5)
    finally:
        stop.set()
        viz.terminate()
        viz.wait(5)
        out = ""
        if proxy is not None:
            proxy.terminate()
            out, _ = proxy.communicate(timeout=5)
    log = [json.loads(l) for f in os.listdir(logdir) if f.endswith(".jsonl")
           for l in open(os.path.join(logdir, f), encoding="utf-8")]
    srcs = {e.get("src") for e in log if e.get("dir") == "c2s"}
    check("proxy jsonl: every C2S packet from the client (viz injected nothing)", srcs == {"client"}, str(srcs))
    ctl_lines = [l for l in out.splitlines() if l.startswith("[proxy] control")]
    check("proxy saw only the test's own control connection (viz never opens it)",
          ctl_lines == [f"[proxy] control {ctl_peer} closed"], str(ctl_lines))
    shutil.rmtree(logdir, ignore_errors=True)


def main():
    logdir = pinned_capture()
    try:
        test_replay_parity(logdir)
        test_order_fallback()
        test_sse(logdir)
        test_burst()
        test_intent_fields()
        test_jobs()
        test_lumber_travel()
        test_hunt_jobs()
        test_overseer_routes(logdir)
        test_live()
    finally:
        shutil.rmtree(logdir, ignore_errors=True)
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    sys.exit(0 if not FAILURES else 1)


if __name__ == "__main__":
    main()

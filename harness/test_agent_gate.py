"""Tests for harness/agent_gate.py and its proxy wiring.

Unit part: a fake clock drives the schedule (forced breaks, idle gaps, the
daily cap, midnight rollover, persistence across restarts, control precedence).
Proxy part: a private proxy (own ports + temp logdir, so the live budget file
is never touched) refuses control-port injections while paused/killed, and a
kill survives a proxy restart.

Run: python harness/test_agent_gate.py   (no game session needed, ~5 s)
"""
import datetime
import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions  # noqa: E402
import agent_gate as ag  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def _free_ports(n):
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return ports


CONTROL_PORT, STATE_PORT, LISTEN_PORT = _free_ports(3)
FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {'' if cond else detail}")
    if not cond:
        FAILURES.append(name)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def fresh(tmp, name, t0):
    clock = Clock(t0)
    return ag.AgentGate(os.path.join(tmp, name), rng=random.Random(7), clock=clock), clock


def run_until_blocked(gate, clock, step=30.0, limit=20 * 3600):
    """Inject every `step` s until the gate closes; returns elapsed seconds."""
    start = clock.t
    while gate.block_reason() is None and clock.t - start < limit:
        gate.record_activity()
        clock.t += step
    return clock.t - start


def unit_tests(tmp):
    # a morning start so the whole capped run (DAILY_CAP_S) stays on one local day
    t0 = datetime.datetime.combine(datetime.date.today(), datetime.time(6, 0)).timestamp()

    print("forced break: due, grace, then the break")
    g, c = fresh(tmp, "brk.json", t0)
    first_interval = g.next_break_after_s
    check("interval jittered within 2h +-20min",
          ag.BREAK_EVERY_S - ag.BREAK_JITTER_S <= first_interval <= ag.BREAK_EVERY_S + ag.BREAK_JITTER_S)
    while g.status()["state"] == "running":
        g.record_activity()
        c.t += 30.0
    st = g.status()
    check("the used-up interval makes the break due, not blocked (the agent may still get home)",
          st["state"] == "break_due" and not st["blocked"] and st["reason"] is None, st)
    check("active time at due ~ the drawn interval",
          first_interval <= g.since_break_s < first_interval + 30.0, g.since_break_s)
    check("status says when it starts by itself",
          ag.BREAK_GRACE_S - 31 <= st["break_starts_in_s"] <= ag.BREAK_GRACE_S, st["break_starts_in_s"])
    due_at = st["break_due_at"]
    run_until_blocked(g, c)
    st = g.status()
    check("unless started earlier, the break starts by itself when the grace runs out",
          st["state"] == "break" and ag.BREAK_GRACE_S <= c.t - due_at < ag.BREAK_GRACE_S + 60.0,
          (st["state"], c.t - due_at))
    brk = st["break_until"] - g.last_active
    check("break length within 3-5 min", ag.BREAK_LEN_S[0] <= brk <= ag.BREAK_LEN_S[1] + 30.0, brk)
    check("reason names the break", "scheduled break" in (st["reason"] or ""), st["reason"])
    check("resume cannot end a break", g.apply("resume") is None and g.status()["state"] == "break")
    c.t = st["break_until"] + 1
    st = g.status()
    check("gate reopens after the break", st["state"] == "running" and st["break_due_at"] is None, st)
    check("break clock restarts with a new jittered interval",
          ag.BREAK_EVERY_S - ag.BREAK_JITTER_S <= st["next_break_in_s"] <= ag.BREAK_EVERY_S + ag.BREAK_JITTER_S,
          st["next_break_in_s"])

    print("the overseer starts a due break itself (once home)")
    g, c = fresh(tmp, "brk_now.json", t0)
    while g.status()["state"] == "running":
        g.record_activity()
        c.t += 30.0
    c.t += 120.0                                    # two minutes of the grace used to walk home
    check("`break` starts it at once", g.apply("break") is None and g.status()["state"] == "break")
    check("a second `break` is refused", g.apply("break") == "already on a break")
    g2 = ag.AgentGate(os.path.join(tmp, "brk_now.json"), rng=random.Random(7), clock=c)
    check("the break survives a restart", g2.status()["state"] == "break")
    c.t = g.break_until + 1
    check("after it: running with a fresh interval",
          g.status()["state"] == "running" and g.status()["next_break_in_s"] >= ag.BREAK_EVERY_S - ag.BREAK_JITTER_S)

    print("idle time")
    g, c = fresh(tmp, "idle.json", t0)
    g.record_activity()
    c.t += 120.0
    g.record_activity()
    check("a gap > ACTIVE_GAP_S is not agent-active time", g.status()["active_today_s"] == 0.0)
    c.t += 30.0
    g.record_activity()
    check("a gap <= ACTIVE_GAP_S is counted", g.status()["active_today_s"] == 30.0)
    for _ in range(120):  # one hour of activity
        c.t += 30.0
        g.record_activity()
    before = g.status()["next_break_in_s"]
    c.t += ag.BREAK_LEN_S[0] + 1  # idle long enough to count as a break
    g.record_activity()
    after = g.status()["next_break_in_s"]
    check("a long idle gap counts as a break taken", after > before + 3000, (before, after))

    print("daily cap")
    g, c = fresh(tmp, "cap.json", t0)
    while True:
        run_until_blocked(g, c)
        st = g.status()
        if st["state"] != "break":
            break
        c.t = st["break_until"] + 1
    check("cap closes the gate", st["state"] == "budget_exhausted", st)
    check("cap reached within one inject gap",
          ag.DAILY_CAP_S <= st["active_today_s"] < ag.DAILY_CAP_S + ag.ACTIVE_GAP_S, st["active_today_s"])
    check("same local day", st["day"] == datetime.date.fromtimestamp(t0).isoformat(), st["day"])
    tomorrow = datetime.datetime.combine(datetime.date.fromtimestamp(t0) + datetime.timedelta(days=1),
                                         datetime.time(0, 0, 1)).timestamp()
    c.t = tomorrow
    st = g.status()
    # the cap can close while a break is due (the 10 h cap, 2026-10-06): that break then runs after midnight
    check("budget reopens at local midnight (the day's count back to 0; at most a due break left)",
          st["state"] in ("running", "break") and st["active_today_s"] == 0.0
          and st["daily_remaining_s"] == ag.DAILY_CAP_S, st)

    print("persistence + controls")
    path = os.path.join(tmp, "persist.json")
    c = Clock(t0)
    g = ag.AgentGate(path, rng=random.Random(1), clock=c)
    for _ in range(100):
        g.record_activity()
        c.t += 30.0
    g.apply("kill")
    used = g.status()["active_today_s"]
    g2 = ag.AgentGate(path, rng=random.Random(2), clock=c)
    check("kill survives a restart", g2.status()["state"] == "killed")
    check("budget survives a restart", g2.status()["active_today_s"] == used, (used, g2.status()))
    g2.apply("pause")
    check("killed outranks paused", g2.status()["state"] == "killed")
    check("resume while killed is refused", g2.apply("resume") is not None)
    g2.apply("rearm")
    check("rearm clears only the kill", g2.status()["state"] == "paused")
    g2.apply("resume")
    check("resume reopens", g2.status()["state"] == "running")
    check("unknown action refused", g2.apply("explode") is not None)
    g3, c3 = fresh(tmp, "brk2.json", t0)
    run_until_blocked(g3, c3)
    g4 = ag.AgentGate(g3.path, clock=c3)
    check("a running break survives a restart", g4.status()["state"] == "break")


def state_req(req):
    with socket.create_connection(("127.0.0.1", STATE_PORT), timeout=5) as s:
        s.sendall((json.dumps(req) + "\n").encode())
        return json.loads(s.makefile("rb").readline())


def control_send(pkt):
    with socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=5) as s:
        s.sendall(len(pkt).to_bytes(2, "big") + pkt)
        f = s.makefile("rb")
        n = int.from_bytes(f.read(2), "big")
        return f.read(n).decode()


def start_proxy(logdir):
    p = subprocess.Popen(
        [PY, os.path.join(HERE, "proxy.py"), "--listen-port", str(LISTEN_PORT),
         "--upstream-host", "127.0.0.1", "--upstream-port", "1",
         "--control-port", str(CONTROL_PORT), "--state-port", str(STATE_PORT), "--logdir", logdir],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", STATE_PORT), timeout=0.2).close()
            return p
        except OSError:
            time.sleep(0.1)
    p.kill()
    raise RuntimeError("proxy did not start")


def proxy_tests(tmp):
    print("proxy wiring")
    logdir = os.path.join(tmp, "logs")
    pkt = actions.single_click(0x1E2)
    p = start_proxy(logdir)
    try:
        st = state_req({"op": "state"})
        check("state responses carry the gate", st.get("gate", {}).get("state") == "running", st)
        check("open gate: injection reaches the session check",
              control_send(pkt) == "ERR no active session")
        r = state_req({"op": "gate", "action": "pause"})
        check("pause via state port", r["ok"] and r["gate"]["state"] == "paused", r)
        check("paused: injection refused", control_send(pkt) == "ERR agent paused")
        state_req({"op": "gate", "action": "resume"})
        state_req({"op": "gate", "action": "kill"})
        check("killed: injection refused", control_send(pkt).startswith("ERR agent killed"))
        r = state_req({"op": "gate", "action": "resume"})
        check("resume while killed refused", not r["ok"] and r["gate"]["killed"], r)
    finally:
        p.terminate()
        p.wait(5)
    check("budget file lives in the proxy's logdir",
          os.path.exists(os.path.join(logdir, "agent_budget.json")))
    p = start_proxy(logdir)
    try:
        check("kill survives a proxy restart", state_req({"op": "gate"})["gate"]["state"] == "killed")
        r = state_req({"op": "gate", "action": "rearm"})
        check("rearm reopens", r["ok"] and r["gate"]["state"] == "running", r)
        check("rearmed: injection passes the gate", control_send(pkt) == "ERR no active session")
    finally:
        p.terminate()
        p.wait(5)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        unit_tests(tmp)
        proxy_tests(tmp)
    print("\nALL PASS" if not FAILURES else f"\nFAILED: {FAILURES}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

"""Bring up the harness's long-lived services and keep them up (user request 2026-10-04;
docs/PLAN.md "One supervisor for the stack", docs/NOTES.md "Stack supervisor").

  python harness/stack.py up [--no-viz] [--no-laya] [--no-bridge] [--no-nat] [--viz-host H]
  python harness/stack.py status
  python harness/stack.py restart <service>
  python harness/stack.py down

`up` runs in the foreground of a console (Ctrl-C there = `down`). It manages:

  proxy   harness/proxy.py, the same arguments as start_proxy_nat.ps1. Up when 127.0.0.1:2593
          listens (bound last, after control 25941 and state 25942).
  nat     harness/divert_nat.py, elevated. Started through start_proxy_nat.ps1 once the proxy is
          up (one UAC prompt). The supervisor can't own an elevated process: it watches the NAT's
          lookup port 25943, and when that drops (or a start fails) it posts an urgent `nat_down`
          juncture and runs the script once per outage (another UAC prompt). `restart nat` asks
          again.
  viz     harness/viz_server.py --live --host 0.0.0.0 (the LAN; allow_viz_lan.ps1), up when :8080
          listens. `--viz-host 127.0.0.1` keeps it on this machine.
  laya    harness/triage.py serve, up when /health on :25970 answers.
  bridge  harness/telegram_bridge.py run (the overseer chat on Telegram); skipped while no bot
          token is configured.

A service already running when `up` starts (its ports listen, or for the bridge its command line
is found) is left alone and watched as `external`; once it goes away, the supervisor starts its
own. A managed service that exits is restarted after a backoff (2, 5, 10, 30, 60 s; reset after
5 min up) and posts one `service_down` juncture per outage (`service_up` when it's back,
`service_flapping` after 5 exits in 10 min). Not managed: the game client (launch_game.ps1, the
user), the overseer session, the hourly backup task, the Discord tooling.

Children get their own process group and a bootstrap that maps SIGBREAK to KeyboardInterrupt, so
a stop is CTRL_BREAK -> each service's own Ctrl-C path (the proxy flushes the memory store), then
`taskkill /T /F` after a grace period, also on the port owners seen while it ran (laya-serve.exe
outlives its Python wrapper otherwise). CTRL_BREAK needs a console: start `up` from one. `down`
stops bridge, laya, viz, then the proxy; the NAT keeps running (elevated; close its window).

Files under logs/stack/: <service>.log (each start appends a header), stack.log, state.json
(written every tick, read by `status`), and the request files `stop` / `restart-<service>`.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIR = os.path.join(ROOT, "logs", "stack")
TICK_S = 1.0
BACKOFF_S = (2, 5, 10, 30, 60)
STABLE_S = 300
FLAP_N, FLAP_WINDOW_S = 5, 600
NAT_PORT = 25943
NAT_SCRIPT = os.path.join(ROOT, "start_proxy_nat.ps1")
NAT_TIMEOUT_S = 90
SERVICES = ("proxy", "viz", "laya", "bridge")
# The child bootstrap: CTRL_BREAK becomes KeyboardInterrupt (Python's default for SIGBREAK is to
# die without cleanup), and the script's folder goes first on sys.path as for `python script.py`.
BOOT = ("import os, runpy, signal, sys; signal.signal(signal.SIGBREAK, signal.default_int_handler); "
        "sys.argv = sys.argv[1:]; sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0]))); "
        "runpy.run_path(sys.argv[0], run_name='__main__')")


def state_path():
    return os.path.join(DIR, "state.json")


def stop_path():
    return os.path.join(DIR, "stop")


def now_s():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    line = f"{now_s()} {msg}"
    print(line, flush=True)
    os.makedirs(DIR, exist_ok=True)
    with open(os.path.join(DIR, "stack.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def listening():
    """{port: pid} of TCP listeners on this machine (netstat; nothing connects to them)."""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    ports = {}
    for line in out.splitlines():
        f = line.split()
        if len(f) >= 5 and f[0] == "TCP" and f[3] == "LISTENING":
            try:
                ports[int(f[1].rsplit(":", 1)[1])] = int(f[4])
            except ValueError:
                pass
    return ports


def pid_alive(pid):
    if not pid:
        return False
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                         capture_output=True, text=True).stdout
    return f'"{pid}"' in out


def kill_tree(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def find_procs(fragment):
    """PIDs of python processes whose command line contains `fragment` (same-user processes'
    command lines are readable without elevation)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
          f"Where-Object {{ $_.CommandLine -like '*{fragment}*' }} | ForEach-Object {{ $_.ProcessId }}")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                             text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(x) for x in out.split() if x.isdigit() and int(x) != os.getpid()]


def laya_ok():
    try:
        with urllib.request.urlopen("http://127.0.0.1:25970/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def bridge_configured():
    """None when the bridge has a token, else why not (telegram.json or UO_TELEGRAM_TOKEN)."""
    import telegram_bridge
    if telegram_bridge.load_config().get("token"):
        return None
    return f"no bot token ({telegram_bridge.CONFIG_PATH} or {telegram_bridge.TOKEN_ENV})"


class Service:
    def __init__(self, name, argv, ports=(), probe=None, find=None, ready_s=60, grace_s=10, skip=None):
        self.name, self.argv, self.ports, self.probe, self.find = name, argv, tuple(ports), probe, find
        self.ready_s, self.grace_s, self.skip = ready_s, grace_s, skip
        self.status = "down"        # down | starting | up | backoff | external | disabled
        self.proc = None
        self.owned = set()          # our pid plus the port owners seen while it ran
        self.external_pid = None
        self.started_t = None
        self.next_t = 0.0
        self.restarts = 0
        self.exits = []             # times of unexpected exits (flap detection)
        self.backoff_i = 0
        self.last_exit = None
        self.note = None
        self.outage_posted = False
        self.flap_posted_t = 0.0

    def healthy(self, ports):
        if any(p not in ports for p in self.ports):
            return False
        return self.probe() if self.probe else True

    def start(self):
        logf = open(os.path.join(DIR, f"{self.name}.log"), "a", encoding="utf-8")
        logf.write(f"\n===== {now_s()} start: {' '.join(self.argv)}\n")
        logf.flush()
        self.proc = subprocess.Popen(
            [sys.executable, "-u", "-c", BOOT, *self.argv], cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        logf.close()
        self.owned = {self.proc.pid}
        self.status, self.started_t, self.note = "starting", time.time(), None
        log(f"{self.name}: started pid {self.proc.pid}")

    def stop(self, why):
        if self.proc is None:
            return
        log(f"{self.name}: stopping pid {self.proc.pid} ({why})")
        if self.proc.poll() is None:
            try:
                os.kill(self.proc.pid, signal.CTRL_BREAK_EVENT)
                self.proc.wait(self.grace_s)
            except (OSError, subprocess.TimeoutExpired):
                log(f"{self.name}: no clean exit within {self.grace_s} s; killing the tree")
        self.reap()

    def reap(self):
        """Kill whatever of ours is left (the tree and the port owners seen while it ran)."""
        for pid in sorted(self.owned):
            if pid_alive(pid):
                kill_tree(pid)
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
        self.proc, self.owned = None, set()

    def to_dict(self):
        pid = self.proc.pid if self.proc is not None else self.external_pid
        return {"status": self.status, "pid": pid, "managed": self.status != "external",
                "since": self.started_t, "restarts": self.restarts, "last_exit": self.last_exit,
                "note": self.note}


def default_services(viz_host="0.0.0.0"):
    viz = ["harness/viz_server.py", "--live", "--host", viz_host]
    return [
        Service("proxy", ["harness/proxy.py", "--nat-lookup-port", "25943", "--upstream-bind", "0.0.0.0",
                          "--upstream-bind-port", "25940", "--logdir", "logs",
                          "--memory-db", "harness/data/harness.db"],
                ports=(25941, 25942, 2593), ready_s=90, grace_s=20),
        Service("viz", viz, ports=(8080,), ready_s=60),
        Service("laya", ["harness/triage.py", "serve"], ports=(25970,), probe=laya_ok, ready_s=240, grace_s=15),
        Service("bridge", ["harness/telegram_bridge.py", "run"], find="telegram_bridge.py run",
                skip=bridge_configured, ready_s=10),
    ]


class Stack:
    def __init__(self, services, disabled=(), nat=True, memory_db=None):
        os.makedirs(DIR, exist_ok=True)
        self.memory_db = memory_db
        self.t0 = time.time()
        self.order = [s.name for s in services]     # start order; stop is the reverse
        self.svcs = {s.name: s for s in services}
        for name in disabled:
            self.svcs[name].status, self.svcs[name].note = "disabled", f"--no-{name}"
        self.nat = {"status": "down" if nat else "disabled", "attempted": False, "posted": False,
                    "proc": None, "since": None, "note": None if nat else "--no-nat"}

    # ------------------------------------------------------------------ alerts
    def post(self, kind, summary, severity, data):
        log(f"juncture {kind} ({severity}): {summary}")
        try:
            import memory
            mem = memory.Memory(self.memory_db or memory.DEFAULT_DB)
            try:
                mem.juncture("stack", kind, summary[:300], severity, data)
            finally:
                mem.con.close()
        except Exception as e:      # the store may be gone (handoff); the log keeps the event
            log(f"juncture not posted: {e!r}")

    # ------------------------------------------------------------------ lifecycle
    def adopt(self, ports):
        """Services already running before `up` are watched, not duplicated."""
        for s in self.svcs.values():
            if s.status == "disabled":
                continue
            if s.ports and all(p in ports for p in s.ports):
                s.status, s.external_pid = "external", ports[s.ports[-1]]
            elif s.find:
                pids = find_procs(s.find)
                if pids:
                    s.status, s.external_pid = "external", pids[0]
            if s.status == "external":
                log(f"{s.name}: already running (pid {s.external_pid}); watching it, not managing it")

    def tick(self, ports):
        t = time.time()
        for name in self.order:
            s = self.svcs[name]
            if s.status == "disabled":
                continue
            if s.status == "external":
                gone = (any(p not in ports for p in s.ports) if s.ports else not pid_alive(s.external_pid))
                if gone:
                    log(f"{name}: the external instance (pid {s.external_pid}) is gone; starting ours")
                    s.status, s.external_pid, s.next_t = "down", None, t
                continue
            if s.proc is not None and s.proc.poll() is not None:
                self.died(s, s.proc.returncode, t)
                continue
            if s.status == "starting":
                if s.healthy(ports):
                    s.status = "up"
                    s.owned |= {ports[p] for p in s.ports if p in ports}
                    log(f"{name}: up after {t - s.started_t:.1f} s")
                    if s.outage_posted:
                        self.post("service_up", f"{name} is back up", "info", {"service": name})
                        s.outage_posted = False
                elif t - s.started_t > s.ready_s:
                    s.stop(f"not ready after {s.ready_s} s")
                    self.died(s, "not ready", t)
            elif s.status == "up":
                s.owned |= {ports[p] for p in s.ports if p in ports}
                if s.backoff_i and t - s.started_t > STABLE_S:
                    s.backoff_i = 0
                if s.ports and any(p not in ports for p in s.ports):
                    s.stop("its port closed")
                    self.died(s, "port closed", t)
            elif s.status in ("down", "backoff") and t >= s.next_t:
                why = s.skip() if s.skip else None
                if why:
                    if s.note != why:
                        log(f"{name}: not started: {why}")
                    s.status, s.note, s.next_t = "down", why, t + 60
                    continue
                busy = [p for p in s.ports if p in ports]
                if busy:
                    s.status, s.external_pid = "external", ports[busy[-1]]
                    log(f"{name}: port {busy[-1]} taken by pid {s.external_pid}; watching it")
                    continue
                s.start()
        self.tick_nat(ports, t)

    def died(self, s, code, t):
        s.last_exit = code
        s.reap()
        s.exits = [x for x in s.exits if t - x < FLAP_WINDOW_S] + [t]
        delay = BACKOFF_S[min(s.backoff_i, len(BACKOFF_S) - 1)]
        s.backoff_i += 1
        s.restarts += 1
        s.status, s.next_t = "backoff", t + delay
        log(f"{s.name}: exited ({code}); restart in {delay} s")
        tail = tail_log(s.name)
        if not s.outage_posted:
            self.post("service_down", f"{s.name} exited ({code}); the supervisor restarts it in {delay} s",
                      "attention", {"service": s.name, "exit": code, "restart_in_s": delay, "log_tail": tail})
            s.outage_posted = True
        if len(s.exits) >= FLAP_N and t - s.flap_posted_t > 3600:
            self.post("service_flapping", f"{s.name} exited {len(s.exits)} times in {FLAP_WINDOW_S // 60} min",
                      "attention", {"service": s.name, "exits": len(s.exits), "log_tail": tail})
            s.flap_posted_t = t

    def tick_nat(self, ports, t):
        n = self.nat
        if n["status"] == "disabled":
            return
        if n["proc"] is not None:                      # start_proxy_nat.ps1 running (UAC prompt)
            rc = n["proc"].poll()
            if rc is None and t - n["since"] < NAT_TIMEOUT_S:
                return
            if rc is None:
                kill_tree(n["proc"].pid)
            n["proc"] = None
            ok = NAT_PORT in listening()
            n["note"] = None if ok else f"start_proxy_nat.ps1 ended ({rc}) without the NAT; see divert.log"
            log(f"nat: {'up' if ok else n['note']}")
            n["status"] = "up" if ok else "down"
            if not ok and not n["posted"]:
                self.post("nat_down", "divert NAT is not running and the restart failed (UAC declined?): game "
                          "traffic doesn't go through the proxy. Run `python harness/stack.py restart nat` or "
                          "start_proxy_nat.ps1", "urgent", {"service": "nat", "script_exit": rc})
                n["posted"] = True
            return
        if NAT_PORT in ports:
            if n["status"] != "up":
                log("nat: up (lookup port 25943 listening)")
                if n["posted"]:
                    self.post("service_up", "divert NAT is back up", "info", {"service": "nat"})
            n.update(status="up", attempted=False, posted=False, note=None)
            return
        if n["status"] == "up":
            log("nat: lookup port 25943 closed: the NAT is down")
            self.post("nat_down", "divert NAT is down: game traffic no longer goes through the proxy; "
                      "a UAC prompt is up to restart it", "urgent", {"service": "nat"})
            n["posted"] = True
        n["status"] = "down"
        if n["attempted"]:
            return                                     # once per outage; `restart nat` asks again
        proxy = self.svcs.get("proxy")
        if proxy is not None and proxy.status not in ("up", "external"):
            n["note"] = "waiting for the proxy (the script would start its own)"
            return
        log("nat: running start_proxy_nat.ps1 (accept the UAC prompt)")
        out = open(os.path.join(DIR, "nat.log"), "a", encoding="utf-8")
        n["proc"] = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", NAT_SCRIPT,
                                      "-Python", sys.executable],
                                     cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        out.close()
        n.update(status="starting", attempted=True, since=t, note="waiting for the UAC prompt")

    def requests(self):
        for name in list(self.svcs) + ["nat"]:
            req = os.path.join(DIR, f"restart-{name}")
            if not os.path.exists(req):
                continue
            os.remove(req)
            if name == "nat":
                self.nat.update(attempted=False)
                log("nat: restart requested")
                continue
            s = self.svcs[name]
            if s.status == "external":
                log(f"{name}: restart requested, but it isn't ours (pid {s.external_pid}); left alone")
            elif s.proc is not None:
                s.stop("restart requested")
                s.status, s.next_t, s.backoff_i = "down", 0.0, 0
            elif s.status in ("down", "backoff"):
                s.next_t = 0.0

    def shutdown(self):
        log("stopping the stack")
        for name in reversed(self.order):
            s = self.svcs[name]
            s.stop("down")
            if s.status not in ("external", "disabled"):
                s.status = "down"
        if self.nat["proc"] is not None:
            kill_tree(self.nat["proc"].pid)
        log("stack down (the divert NAT keeps running in its elevated window)")

    def write_state(self, stopped=False):
        st = {"pid": os.getpid(), "started": self.t0, "t": time.time(),
              "services": {n: self.svcs[n].to_dict() for n in self.order},
              "nat": {"status": self.nat["status"], "managed": False, "note": self.nat["note"]}}
        if stopped:
            st["stopped"] = time.time()
        part = state_path() + ".part"
        with open(part, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=1)
        os.replace(part, state_path())

    def step(self):
        """One supervision tick: requests, health, restarts, state file. False once stop is asked."""
        if os.path.exists(stop_path()):
            return False
        self.requests()
        self.tick(listening())
        self.write_state()
        return True

    def run(self):
        if os.path.exists(stop_path()):
            os.remove(stop_path())
        self.adopt(listening())
        try:
            while self.step():
                time.sleep(TICK_S)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()
            if os.path.exists(stop_path()):
                os.remove(stop_path())
            self.write_state(stopped=True)


def tail_log(name, n=15):
    """The last n lines of a service log (reads only its end: the proxy's log grows large)."""
    p = os.path.join(DIR, f"{name}.log")
    if not os.path.exists(p):
        return []
    with open(p, "rb") as f:
        f.seek(max(0, os.path.getsize(p) - 8192))
        return [ln.rstrip() for ln in f.read().decode("utf-8", "replace").splitlines()[-n:]]


def read_state():
    if not os.path.exists(state_path()):
        return None
    with open(state_path(), encoding="utf-8") as f:
        return json.load(f)


def running_supervisor():
    st = read_state()
    if st and not st.get("stopped") and time.time() - st.get("t", 0) < 30 and pid_alive(st.get("pid")):
        return st
    return None


def fmt_s(s):
    s = int(s)
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


def status():
    st = read_state()
    if not st:
        print("no supervisor has run yet (python harness/stack.py up)")
        return 1
    live = running_supervisor()
    print(f"supervisor pid {st['pid']}, up {fmt_s(time.time() - st['started'])}" if live
          else f"supervisor not running (last state {fmt_s(time.time() - st.get('t', 0))} ago)")
    for name, s in list(st["services"].items()) + [("nat", st["nat"])]:
        extra = ", ".join(x for x in (
            f"pid {s['pid']}" if s.get("pid") else "",
            f"up {fmt_s(time.time() - s['since'])}" if s.get("since") and s["status"] == "up" else "",
            f"restarts {s['restarts']}" if s.get("restarts") else "",
            f"last exit {s['last_exit']}" if s.get("last_exit") is not None else "",
            "not ours" if s["status"] == "external" else "",
            s.get("note") or "") if x)
        print(f"  {name:7} {s['status']:9} {extra}")
    return 0 if live else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="bring up and supervise the harness services")
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("up", help="start everything and keep it up (foreground; Ctrl-C = down)")
    for name in ("viz", "laya", "bridge", "nat"):
        up.add_argument(f"--no-{name}", action="store_true", help=f"don't run {name}")
    up.add_argument("--viz-host", default="0.0.0.0",
                    help="viz_server --host (default 0.0.0.0, the LAN: run allow_viz_lan.ps1 once; 127.0.0.1 = local only)")
    sub.add_parser("status", help="what runs, as the supervisor last saw it")
    rs = sub.add_parser("restart", help="ask the running supervisor to restart one service")
    rs.add_argument("service", choices=list(SERVICES) + ["nat"])
    sub.add_parser("down", help="ask the running supervisor to stop everything it runs")
    a = ap.parse_args(argv)
    os.makedirs(DIR, exist_ok=True)
    if a.cmd == "up":
        live = running_supervisor()
        if live:
            print(f"a supervisor already runs (pid {live['pid']}): python harness/stack.py status", file=sys.stderr)
            return 1
        sys.path.insert(0, HERE)
        disabled = [n for n in ("viz", "laya", "bridge") if getattr(a, f"no_{n}")]
        Stack(default_services(a.viz_host), disabled=disabled, nat=not a.no_nat).run()
        return 0
    if a.cmd == "status":
        return status()
    if not running_supervisor():
        print("no supervisor is running", file=sys.stderr)
        return 1
    if a.cmd == "restart":
        open(os.path.join(DIR, f"restart-{a.service}"), "w").close()
        print(f"restart of {a.service} requested")
        return 0
    open(stop_path(), "w").close()
    print("stop requested; the supervisor stops bridge, laya, viz, then the proxy")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Agent gate: pause, kill switch, mandatory breaks and the daily budget.

The proxy consults the gate before every agent injection on the control port
(docs/PLAN.md Phase 4, user decisions 2026-09-29). While the gate is closed
every agent packet is refused with `ERR <reason>`; client traffic and the
relay are never affected, and the gate never sends anything to the server.

Closed when (precedence order):
  killed            kill switch; latched, persisted, cleared only by `rearm`
  paused            manual pause (visualizer button / CLI); `resume` clears it
  break             forced break: after BREAK_EVERY_S (+-BREAK_JITTER_S, drawn
                    per cycle) of agent-active time, a BREAK_LEN_S wall-clock
                    break; `resume` cannot end it
  budget_exhausted  DAILY_CAP_S of agent-active time used on this local
                    calendar day; reopens at local midnight

Open but announced:
  break_due         the break interval is used up. The agent may still act for
                    up to BREAK_GRACE_S, so the overseer (woken by a `break_due`
                    juncture from `ctl wait`) can finish or stop the task and
                    get somewhere safe, then start the break itself with the
                    `break` action (`ctl break`). Otherwise the break starts by
                    itself when the grace runs out, wherever the character is.
                    `break` also starts a break early at any time.

Agent-active time: the gaps between consecutive accepted injections that are
at most ACTIVE_GAP_S apart. Idle time, paused time and breaks don't count. A
gap of at least the minimum break length counts as a break taken (the break
clock restarts), so an agent that already rested isn't forced to rest again.

State is persisted (atomic JSON) so a proxy restart resets neither the
budget, a running break, nor a kill. The proxy keeps the file in its logdir
(`<logdir>/agent_budget.json`, override with --budget-file), so tests that
run private proxies with their own logdirs never touch the live budget.

CLI (talks to the proxy's state port):
  python harness/agent_gate.py [status|pause|resume|kill|rearm] [--state-port 25942]
"""
import argparse
import datetime
import json
import os
import random
import socket
import time

DAILY_CAP_S = 8 * 3600.0
BREAK_EVERY_S = 2 * 3600.0
BREAK_JITTER_S = 20 * 60.0
BREAK_LEN_S = (15 * 60.0, 30 * 60.0)
BREAK_GRACE_S = 10 * 60.0
ACTIVE_GAP_S = 60.0
SAVE_EVERY_S = 10.0

ACTIONS = ("pause", "resume", "kill", "rearm", "break")
REARM_HINT = "python harness/agent_gate.py rearm"


def local_day(t: float) -> str:
    return datetime.date.fromtimestamp(t).isoformat()


class AgentGate:
    def __init__(self, path: str, rng: random.Random | None = None, clock=time.time):
        self.path = path
        self.rng = rng or random.Random()
        self.clock = clock
        now = clock()
        self.day = local_day(now)
        self.active_today_s = 0.0
        self.since_break_s = 0.0
        self.next_break_after_s = self._draw_interval()
        self.break_until: float | None = None
        self.break_due_at: float | None = None   # break interval used up; grace until +BREAK_GRACE_S
        self.last_active: float | None = None
        self.paused = False
        self.killed = False
        self._saved_at = 0.0
        self._dirty = False
        self._load()

    # -- persistence -----------------------------------------------------------
    _FIELDS = ("day", "active_today_s", "since_break_s", "next_break_after_s",
               "break_until", "break_due_at", "last_active", "paused", "killed")

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        for k in self._FIELDS:
            if k in data:
                setattr(self, k, data[k])

    def save(self, force: bool = False):
        now = self.clock()
        if not force and (not self._dirty or now - self._saved_at < SAVE_EVERY_S):
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: getattr(self, k) for k in self._FIELDS}, f, indent=1)
        os.replace(tmp, self.path)
        self._saved_at = now
        self._dirty = False

    # -- schedule --------------------------------------------------------------
    def _draw_interval(self) -> float:
        return BREAK_EVERY_S + self.rng.uniform(-BREAK_JITTER_S, BREAK_JITTER_S)

    def _break_taken(self):
        self.since_break_s = 0.0
        self.next_break_after_s = self._draw_interval()
        self.break_due_at = None

    def _start_break(self, now: float):
        self.break_until = now + self.rng.uniform(*BREAK_LEN_S)
        self.break_due_at = None
        self.save(force=True)

    def _settle(self, now: float):
        """Apply day rollover, the end of a due break's grace, and break expiry."""
        today = local_day(now)
        if today != self.day:
            self.day = today
            self.active_today_s = 0.0
            self._dirty = True
        if self.break_due_at is not None and self.break_until is None and now >= self.break_due_at + BREAK_GRACE_S:
            self._start_break(now)
        if self.break_until is not None and now >= self.break_until:
            self.break_until = None
            self._break_taken()
            self.save(force=True)

    def block_reason(self, now: float | None = None) -> str | None:
        """Why agent injection is refused right now, or None when open."""
        return self.status(now)["reason"]

    def record_activity(self, now: float | None = None):
        """Account one accepted agent injection."""
        now = self.clock() if now is None else now
        self._settle(now)
        if self.last_active is not None:
            gap = now - self.last_active
            if 0 <= gap <= ACTIVE_GAP_S:
                self.active_today_s += gap
                self.since_break_s += gap
            elif gap >= BREAK_LEN_S[0]:
                self._break_taken()
        self.last_active = now
        self._dirty = True
        if self.since_break_s >= self.next_break_after_s and self.break_due_at is None and self.break_until is None:
            self.break_due_at = now
            self.save(force=True)
        elif self.active_today_s >= DAILY_CAP_S:
            self.save(force=True)
        else:
            self.save()

    # -- controls --------------------------------------------------------------
    def apply(self, action: str) -> str | None:
        """pause / resume / kill / rearm / break; returns an error string or None."""
        if action == "pause":
            self.paused = True
        elif action == "resume":
            if self.killed:
                return f"agent killed; rearm first ({REARM_HINT})"
            self.paused = False
        elif action == "kill":
            self.killed = True
        elif action == "rearm":
            self.killed = False
        elif action == "break":
            now = self.clock()
            self._settle(now)
            if self.break_until is not None:
                return "already on a break"
            self._start_break(now)
            return None
        else:
            return f"unknown gate action {action!r}"
        self.save(force=True)
        return None

    def status(self, now: float | None = None) -> dict:
        now = self.clock() if now is None else now
        self._settle(now)
        in_break = self.break_until is not None
        if self.killed:
            state, reason = "killed", f"agent killed (rearm: {REARM_HINT})"
        elif self.paused:
            state, reason = "paused", "agent paused"
        elif in_break:
            until = datetime.datetime.fromtimestamp(self.break_until).strftime("%H:%M:%S")
            state, reason = "break", f"scheduled break until {until}"
        elif self.active_today_s >= DAILY_CAP_S:
            state = "budget_exhausted"
            reason = (f"daily agent budget exhausted ({DAILY_CAP_S / 3600:g} h); "
                      "reopens at local midnight")
        elif self.break_due_at is not None:
            state, reason = "break_due", None
        else:
            state, reason = "running", None
        return {
            "state": state, "blocked": reason is not None, "reason": reason,
            "paused": self.paused, "killed": self.killed,
            "break_until": self.break_until,
            "break_due_at": self.break_due_at,
            "break_starts_in_s": None if self.break_due_at is None
            else max(0.0, self.break_due_at + BREAK_GRACE_S - now),
            "next_break_in_s": None if in_break
            else max(0.0, self.next_break_after_s - self.since_break_s),
            "active_today_s": self.active_today_s, "daily_cap_s": DAILY_CAP_S,
            "daily_remaining_s": max(0.0, DAILY_CAP_S - self.active_today_s),
            "day": self.day, "now": now,
        }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Inspect or change the proxy's agent gate.")
    ap.add_argument("action", nargs="?", default="status", choices=("status",) + ACTIONS)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--state-port", type=int, default=25942)
    args = ap.parse_args(argv)
    req = {"op": "gate"}
    if args.action != "status":
        req["action"] = args.action
    with socket.create_connection((args.host, args.state_port), timeout=5) as s:
        s.sendall((json.dumps(req) + "\n").encode())
        resp = json.loads(s.makefile("rb").readline())
    print(json.dumps(resp, indent=1))
    return 0 if resp.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

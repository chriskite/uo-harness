"""Lumber loop runner, Shelter phase (docs/LUMBER_LOOP.md §3, §12).

One trip = harvest known trees → convert logs to boards → walk to the inn →
say "room" → enter the rental room → drop the boards into the secure
container → leave by the door. No deed creation (user decision 2026-09-29:
prove the loop first).

Knowledge comes from harness/data/loops/lumber.json (mined from the user's
demonstration). What the loop learns lives in the harness memory store
(harness/memory.py, docs/MEMORY.md): per-tree attempts/yield/depletion/
reachability, every attempt, and one episode row per trip. Walk memory is
recorded by the proxy.

Captcha (ANTICHEAT.md §8.8/§8.13). The real captcha is the
gump with lumber.json's id plus a text entry and the submit button. The runner
auto-solves it: captcha.solve reads the digits from the gump layout
(harness/captcha.py, ANTICHEAT.md §8.8) and answers with the stock 0xB1. If the
layout is unreadable or answers get rejected, it falls back to pausing and
beeping until a solve is observed in the client. The runner never replies to a
gump without a reply button (the decoys).

The only other gump the runner answers is the rental-room menu, and only with
Enter/Exit: an innkeeper menu without the rented-room buttons (Test Shard
wipe) aborts instead of clicking Rent. The only speech is "room".

Guards: jittered pacing, overall timeout, HP loss, movement stall, the agent
gate (pause/break wait, kill/budget abort), bounded retries everywhere.

Run:  python harness/loop_lumber.py [--trips 1] [--logs-per-trip 15]
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions  # noqa: E402
from agent_link import Abort, Link, Mover, cheb, containers_to_open, log, reach_z, same_floor, serial_of  # noqa: E402
import uomap  # noqa: E402
from humanize import PROFILES, Human  # noqa: E402
from uo.gumps import parse_layout  # noqa: E402
from memory import DEFAULT_DB, Memory  # noqa: E402
import ledger as ledger_mod  # noqa: E402
import threats  # noqa: E402
from speech_guard import SpeechGuard, staff_hints  # noqa: E402
import alerts  # noqa: E402
import captcha  # noqa: E402

TREE_FACET = 0                # harvest areas are on map0 (Shelter)
RECALL_S = 2.0                # Recall cast time (docs/research/TRAVEL_DEATH.md)
SPEECH_POLL_S = 1.0           # while paused for speech: state reads + all-clear checks
THREAT_MARGIN_S = 1.0         # reaction + packet latency on top of the cast

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "harness", "data")
LAYER_BACKPACK = 0x15
HATCHETS = (0x0F43, 0x0F44)
LOGS = tuple(range(0x1BDD, 0x1BE3))
BOARDS = (0x1BD7,)
DROP_AUTO = 0x7FFFFFFF        # client drop-into-container auto-position (demo)
ROOM_RENTED_BUTTON = 7        # present in the innkeeper/door menu only while a room is rented


def h(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def alert(sound: bool = True):
    """Captcha alert sound (alerts.handoff); interim handoff pending auto-solve."""
    alerts.handoff(sound)


class LumberLoop:
    def __init__(self, link: Link, memory: Memory, know: dict, args):
        self.link = link
        self.k = know
        self.args = args
        self.deadline = time.monotonic() + args.timeout
        self.start_hits = None
        self.human = Human(args.human, seed=args.seed, fast=args.human_fast, log=log)
        self.mover = Mover(link, memory, self.human, max_blocked=args.max_blocked,
                           guard=self.check_guards, doors=True, use_map=not args.no_map)
        self.memory = memory
        self.t_exit = None           # wall time of the last teleport out of the room
        self.stats = {}
        self.trip_n = None
        # a creature is a threat when it's in war mode, murderer-red or known aggressive
        # (threats.Params); a wandering goat isn't
        self.watch = threats.Watch(threats.Params())
        self.last_threats = None
        self.seen_hostiles = set()
        self.ledger = ledger_mod.Ledger()
        self._intent = None          # last reported (kind, text, target), restored after a captcha
        self.speech = SpeechGuard()  # a character speaking near us hands control to the overseer

    def doing(self, kind: str, text: str, target=None):
        """Tell the visualizer what the agent is trying to do (proxy-side only)."""
        self._intent = (kind, text, target)
        self.link.intent(text, kind, target, loop="lumber", trip=self.trip_n, trips=self.args.trips)

    # ------------------------------------------------------------ guards
    def check_guards(self, st: dict):
        if time.monotonic() > self.deadline:
            raise Abort(f"overall timeout ({self.args.timeout}s)")
        mv = st["movement"]
        if mv["stalled"]:
            raise Abort(f"movement stalled ({mv['rejects_in_row']} walks rejected in a row)")
        self.check_threats(st)
        self.check_ledger(st)
        self.check_speech(st)
        hits = st["world"]["self"].get("hits")
        if hits is not None:
            if self.start_hits is None:
                self.start_hits = hits
            elif hits < self.start_hits:
                raise Abort(f"hit points dropped ({self.start_hits} -> {hits}); stopping")

    def _where(self, st):
        pos = st["movement"]["pos"] or [None, None]
        return {"facet": st["world"]["self"].get("map"), "x": pos[0], "y": pos[1]}

    def check_threats(self, st):
        """threats.py over every state read: log hostile players once each
        (pk_seen); stop on a flee-level threat or when hurt. There is no escape
        yet (recall needs the runebook demo), so stopping and waking the
        overseer is the response."""
        a = self.watch.update(st, recall_s=RECALL_S, margin_s=THREAT_MARGIN_S)
        self.last_threats = a
        if a.dead:
            self.died(st, "ghost body")
        for t in a.threats:
            if t.hostile and t.player and t.serial not in self.seen_hostiles:
                self.seen_hostiles.add(t.serial)
                self.memory.job_event("lumber", "pk_seen", t.to_dict(), **self._where(st))
        if a.flee or a.under_attack:
            worst = a.flee[0] if a.flee else None
            summary = (f"{worst.kind} {worst.name or hex(worst.serial)} at {worst.distance} tiles "
                       f"(ETA {worst.eta_s:.1f} s)" if worst else "taking damage")
            data = a.to_dict()
            self.memory.juncture("lumber", "threat", f"Threat: {summary}", "urgent", data)
            self.memory.job_event("lumber", "flee", data, **self._where(st))
            raise Abort(f"threat: {summary}; stopping (no escape action yet)")

    def check_ledger(self, st):
        """ledger.py over every state read: unexplained pack losses are
        reported as suspected theft (the loop carries on); death stops."""
        d = self.ledger.observe(st)
        if d.death:
            self.died(st, d.death_reason or "pack emptied")
        if d.theft_suspected:
            lost = d.unexplained_losses
            n = sum(e.get("amount") or 1 for e in lost)
            what = ", ".join(sorted({e.get("wood") or f"0x{e['graphic']:04X}" for e in lost}))
            self.memory.juncture("lumber", "theft_suspected", f"{n} item(s) left the pack unexplained: {what}",
                                 "attention", d.to_dict())
            self.memory.job_event("lumber", "theft", {"amount": n, "items": lost}, **self._where(st))
            log(f"pack lost {n} item(s) without a cause ({what}); suspected theft, carrying on")

    def check_speech(self, st):
        """speech_guard.py: a character speaking near us hands control to the
        overseer (user request 2026-10-01, harvest jobs only)."""
        who = self.speech.scan(st["world"], self.link.events, self.link.event_t)
        if who:
            self.speech_hold(who, st)

    def speech_hold(self, who, st):
        """Send nothing until the overseer gives the all-clear (acks the
        `speech_nearby` juncture) and no `gm_suspected` juncture is open.
        Threats and death still end the job. The overseer may talk to the
        speaker meanwhile (ctl allows `act say` and `single_click` while a task
        holds), or stop the job. A speaker with staff hints (speech_guard.
        staff_hints) raises `gm_suspected` and the staff alarm, which repeats
        (alerts.STAFF_REPEAT_S) until that juncture is acked."""
        first = who[0]
        name = first["label"] or first["name"] or first["serial"]
        log(f"SPEECH: {name}: {first['text']!r}; pausing for the overseer")
        resume = self._intent
        self.doing("speech", f"Paused: {name} spoke nearby; waiting for the overseer")
        data = {"hold": True, "task": "lumber", "trip": self.trip_n, "speakers": who, **self._where(st)}
        jid = self.memory.juncture("lumber", "speech_nearby",
                                   f"{name} said {first['text']!r} nearby; harvesting paused until the "
                                   f"all-clear (ack)"[:300], "urgent", data)
        self.memory.job_event("lumber", "speech_hold", data, **self._where(st))
        if not self.suspect_staff(who, st):
            alert(not self.args.quiet)
        t0, heard = time.monotonic(), {w["serial"] for w in who}
        while True:
            time.sleep(SPEECH_POLL_S)
            st = self.link.state()
            self.check_threats(st)
            self.check_ledger(st)
            new = self.speech.scan(st["world"], self.link.events, self.link.event_t)
            for w in new:
                log(f"SPEECH (paused): {w['label'] or w['name'] or w['serial']}: {w['text']!r}")
                heard.add(w["serial"])
            self.suspect_staff(new, st)
            alerts.staff_alarm_due(self.memory, not self.args.quiet)
            j = self.memory.junctures(after_id=jid - 1, limit=1)
            if j and j[0]["acked_t"] is not None and not alerts.open_gm(self.memory):
                break
        waited = time.monotonic() - t0
        self.deadline += waited                      # the pause isn't the job's time
        self.speech.clear(heard)
        self.stats["speech_holds"] = self.stats.get("speech_holds", 0) + 1
        self.stats["speech_wait_s"] = self.stats.get("speech_wait_s", 0.0) + waited
        log(f"all-clear after {waited:.0f} s; resuming")
        self.human.wait("read")
        if resume is not None:
            self.doing(*resume)

    def suspect_staff(self, who, st) -> bool:
        """Raise gm_suspected (urgent, staff alarm) for speakers with staff hints,
        unless one is already open. True when one is open afterwards."""
        hinted = [w for w in who if staff_hints(w)]
        if hinted and not alerts.open_gm(self.memory):
            w = hinted[0]
            name = w["label"] or w["name"] or w["serial"]
            log(f"POSSIBLE STAFF: {name} ({', '.join(staff_hints(w))}); staff alarm")
            alerts.post_gm(self.memory, "lumber", f"{name}: {', '.join(staff_hints(w))}",
                           {"task": "lumber", "trip": self.trip_n, "speakers": hinted, **self._where(st)},
                           not self.args.quiet)
        return bool(alerts.open_gm(self.memory))

    def died(self, st, reason):
        a = self.last_threats
        players = [t for t in (a.threats if a else []) if t.hostile and t.player]
        cause = "pk" if players else ("mob" if a and a.under_attack else "unknown")
        data = {"reason": reason, "cause": cause, "threats": a.to_dict() if a else None}
        self.memory.juncture("lumber", "death", f"Died ({cause}): {reason}", "urgent", data)
        self.memory.job_event("lumber", "death", data, **self._where(st))
        raise Abort(f"died ({cause}): {reason}")

    def state(self):
        st = self.link.state()
        self.check_guards(st)
        return st

    # ------------------------------------------------------------ world lookups
    def self_serial(self, st) -> int:
        return st["movement"]["self_serial"]

    def item(self, st, serial):
        return st["world"]["items"].get(f"0x{serial:08X}")

    def backpack(self, st) -> int:
        me = self.self_serial(st)
        for key, it in st["world"]["items"].items():
            if it.get("layer") == LAYER_BACKPACK and it.get("container") is not None \
                    and serial_of(it["container"]) == me:
                return serial_of(key)
        raise Abort("backpack not known to the world model")

    def in_pack(self, st, graphics):
        pack = self.backpack(st)
        return [(serial_of(k), it) for k, it in st["world"]["items"].items()
                if it.get("graphic") in graphics and it.get("container") is not None
                and serial_of(it["container"]) == pack]

    def count(self, st, graphics) -> int:
        return sum(it.get("amount") or 1 for _, it in self.in_pack(st, graphics))

    def hatchet(self, st) -> int:
        me, pack = self.self_serial(st), self.backpack(st)
        for key, it in st["world"]["items"].items():
            if it.get("graphic") in HATCHETS and it.get("container") is not None \
                    and serial_of(it["container"]) in (me, pack):
                return serial_of(key)
        raise Abort("no hatchet worn or in the backpack")

    # ------------------------------------------------------------ event scans
    def since(self, mark):
        return self.link.events[mark:]

    def heard(self, mark, text=None, cliloc=None):
        for ev in self.since(mark):
            if text is not None and ev.get("ev") == "speech_heard" and ev.get("text") == text:
                return ev
            if cliloc is not None and ev.get("ev") == "cliloc" and ev.get("cliloc") == cliloc:
                return ev
        return None

    def gump(self, mark, gump_id):
        for ev in self.since(mark):
            if ev.get("ev") == "gump_open" and ev.get("gump_id") == gump_id:
                return ev
        return None

    def real_captcha(self, mark):
        """The real captcha: lumber.json's gump id with the text entry and a
        reply button besides Guide (the submit id is random per captcha).
        Decoys (same words, no buttons) never match."""
        cap = self.k["captcha"]
        for i, ev in enumerate(self.since(mark)):
            if ev.get("ev") == "gump_open" and ev.get("gump_id") == h(cap["gump_id"]):
                lay = parse_layout(ev.get("layout", ""))
                if cap["answer_entry_id"] in lay["entries"] \
                        and any(b != cap["guide_button"] for b in lay["buttons"]):
                    return mark + i
        return None

    def cursor(self, mark):
        for ev in self.since(mark):
            if ev.get("ev") == "target":
                return ev
        return None

    # ------------------------------------------------------------ captcha
    def captcha_handoff(self, idx):
        """Auto-solve: captcha.solve reads the digits from the gump layout
        (harness/captcha.py, ANTICHEAT.md §8.8) and the runner answers with the
        stock 0xB1 after a human-plausible delay. A wrong answer costs a strike
        (the server re-opens a fresh captcha); unreadable layouts and repeated
        strikes fall back to pause + beep until a solve is observed."""
        cap = self.k["captcha"]
        self.stats["captchas"] = self.stats.get("captchas", 0) + 1
        t0 = time.monotonic()
        resume = self._intent
        strikes = 0
        ev = self.link.events[idx]
        while strikes <= self.args.captcha_max_strikes:
            digits = captcha.solve(ev.get("layout", ""))
            submit = captcha.submit_button(ev.get("layout", ""), cap["guide_button"])
            if digits is None or submit is None:
                break                          # unreadable: fallback below
            self.doing("captcha", "Solving the captcha")
            self.human.wait("captcha")
            mark = len(self.link.events)
            self.link.act(actions.gump_reply(ev["serial"], h(cap["gump_id"]), submit,
                                             ev.get("layout", ""), ev.get("lines") or [],
                                             texts={cap["answer_entry_id"]: digits}))
            log(f"captcha answered {digits!r} (auto-solved from the layout)")
            end = time.monotonic() + 12.0
            while time.monotonic() < end:
                self.link.state()
                if self.heard(mark, text=cap["ok_text"]):
                    waited = time.monotonic() - t0
                    self.stats["captcha_wait_s"] = self.stats.get("captcha_wait_s", 0.0) + waited
                    log(f"captcha solved in {waited:.0f} s (auto); resuming")
                    self.human.wait("read")
                    if resume is not None:
                        self.doing(*resume)
                    return
                nxt = self.real_captcha(mark)
                if nxt is not None:            # rejected: a fresh captcha opened
                    strikes += 1
                    log(f"captcha answer rejected (strike {strikes})")
                    ev = self.link.events[nxt]
                    break
                time.sleep(0.3)
            else:
                raise Abort("captcha answer got no server reply within 12 s")
        # fallback: pause + beep until a solve is observed in the client
        log("CAPTCHA up: agent paused, waiting for the solve (auto-solve could not read it)")
        self.doing("captcha", "Captcha up — paused until it is solved")
        jid = self.memory.juncture("lumber", "captcha", "Captcha up; agent paused until solved (auto-solve could not read it)",
                                   "urgent", {"trip": self.trip_n})
        alert(not self.args.quiet)
        next_beep = t0 + self.args.captcha_beep_s
        while True:
            self.link.state()
            if self.heard(idx, text=cap["ok_text"]):
                waited = time.monotonic() - t0
                self.stats["captcha_wait_s"] = self.stats.get("captcha_wait_s", 0.0) + waited
                log(f"captcha solved after {waited:.0f} s; resuming")
                self.memory.juncture_ack(jid)          # solved; nothing left for the overseer
                self.human.wait("read")
                if resume is not None:
                    self.doing(*resume)
                return
            now = time.monotonic()
            if now - t0 > self.args.captcha_timeout:
                raise Abort(f"captcha not solved within {self.args.captcha_timeout:.0f} s")
            if now >= next_beep:
                alert(not self.args.quiet)
                next_beep = now + self.args.captcha_beep_s
            time.sleep(0.5)

    # ------------------------------------------------------------ using the hatchet
    def use_hatchet(self):
        """dclick the hatchet; returns the target-cursor event (captchas handled).
        Now and then the human hesitates: cancels the cursor (stock Esc packet)
        and uses the hatchet again."""
        for attempt in range(2):
            self.human.wait("use")
            st = self.state()
            mark = len(self.link.events)
            self.link.act(actions.dclick(self.hatchet(st)))
            self.link.wait(lambda s: self.cursor(mark) or self.real_captcha(mark) is not None, 4.0)
            cap = self.real_captcha(mark)
            if cap is not None:
                self.captcha_handoff(cap)
                return None
            cur = self.cursor(mark)
            if cur is None or attempt == 1 or not self.human.hesitate():
                return cur
            log("(hesitating: cancelling the cursor)")
            self.human.wait("aim")
            self.link.act(actions.target_cancel(cur["cursor_id"], cur["target_type"], cur["cursor_type"]))
        return None

    # ------------------------------------------------------------ harvesting
    def outcome(self, mark):
        hv, lock = self.k["harvest"], self.k["travel_lockout"]
        for ev in self.since(mark):
            e = ev.get("ev")
            if e == "speech_heard":
                t = ev.get("text") or ""
                if t == hv["success_text"]:
                    return ("success", 0)
                if t.startswith(lock["text_prefix"]):
                    digits = [int(w) for w in t.split() if w.isdigit()]
                    return ("lockout", digits[0] if digits else lock["seconds"])
            elif e == "cliloc":
                n = ev.get("cliloc")
                if n == hv["fail_cliloc"]:
                    return ("fail", 0)
                if n in hv["depleted_clilocs"]:
                    return ("depleted", 0)
                if n == hv["not_a_tree_cliloc"]:
                    return ("not_tree", 0)
        return None

    def attempt(self, tree):
        """One harvest attempt on `tree` → (outcome, logs gained)."""
        st = self.state()
        before = self.count(st, LOGS)
        cur = self.use_hatchet()
        if cur is None:
            return ("captcha", 0)
        self.human.wait("aim")
        mark = len(self.link.events)
        self.link.act(actions.target_xyz(cur["cursor_id"], tree["x"], tree["y"], tree["z"],
                                         h(tree["graphic"]), cursor_type=cur["cursor_type"]))
        end = time.monotonic() + self.args.attempt_timeout
        while time.monotonic() < end:
            self.state()
            cap = self.real_captcha(mark)
            if cap is not None:
                self.captcha_handoff(cap)
                mark = cap + 1               # the server finishes the attempt after the answer
                end = time.monotonic() + self.args.attempt_timeout
                continue
            out = self.outcome(mark)
            if out is not None:
                if out[0] == "success":
                    st = self.link.wait(lambda s: self.count(s, LOGS) > before, 3.0)
                    gained = self.count(st or self.link.state(), LOGS) - before
                    return ("success", max(gained, 0))
                return out
            time.sleep(0.15)
        return ("none", 0)

    def wait_lockout(self):
        """The server blocks harvesting for 60 s after a teleport (room exit)."""
        if self.t_exit is None:
            return
        left = self.k["travel_lockout"]["seconds"] - (time.time() - self.t_exit)
        if left > 0:
            wait = left + self.human.reaction("read") + self.human.rng.uniform(0.5, 3.0)
            log(f"travel lockout: waiting {wait:.0f} s before harvesting")
            self.doing("lockout", f"Waiting out the travel lockout ({wait:.0f} s) before chopping")
            time.sleep(wait)

    def candidate_trees(self, st):
        """Seed trees (lumber.json) plus trees found on the map in the harvest
        area, minus what harvest memory rules out, nearest first with human
        noise, at most --max-trees per trip."""
        seeds = list(self.k["harvest"]["trees"])
        found = []
        area = self.k["harvest"].get("area")
        walk = self.mover.walk_map(st)
        if area and walk is not None:
            cx, cy = area["center"]
            r = area["radius"]
            found = [{"x": x, "y": y, "z": z, "graphic": f"0x{g:04X}"}
                     for x, y, z, g in walk.m.find_trees(cx - r, cy - r, cx + r, cy + r)]
        seen, trees = set(), []
        for t in seeds + found:
            if (t["x"], t["y"]) not in seen:
                seen.add((t["x"], t["y"]))
                trees.append(t)
        now = time.time()
        trees = [t for t in trees if self.memory.harvest_available(
            TREE_FACET, t["x"], t["y"], t["z"], self.args.regrow_min * 60, now)]
        pos = st["movement"]["pos"]
        trees.sort(key=lambda t: cheb(pos, (t["x"], t["y"])) * self.human.rng.uniform(1.0, 1.6))
        return trees[: self.args.max_trees]

    def harvest_trip(self) -> int:
        trees = self.candidate_trees(self.state())
        if not trees:
            raise Abort("no harvestable tree available (all depleted, unreachable or ruled out)")
        gained = attempts = successes = unknown = 0
        for tree in trees:
            if gained >= self.args.logs_per_trip:
                break
            label = f"tree {tree['x']},{tree['y']}"
            node = (TREE_FACET, tree["x"], tree["y"], tree["z"], h(tree["graphic"]))
            spot = (tree["x"], tree["y"])
            quota = f"{gained}/{self.args.logs_per_trip} logs"
            self.doing("to_tree", f"Heading to tree at {spot[0]},{spot[1]} ({quota})", spot)
            z_ok = self.tree_z_ok(tree)
            try:
                if "stand" in tree:
                    self.mover.walk_to(lambda: tree["stand"], 0, f"to {label}", z_ok=z_ok)
                else:
                    self.mover.walk_to(lambda: (tree["x"], tree["y"]), 1, f"to {label}", z_ok=z_ok)
            except Abort as e:
                if "no route" not in str(e):
                    raise
                self.memory.harvest_record(*node, "unreachable")
                log(f"{label}: unreachable; trying the next tree")
                continue
            self.wait_lockout()
            tries = 0
            while tries < self.args.max_attempts_per_tree and gained < self.args.logs_per_trip:
                self.doing("chop", f"Chopping tree at {spot[0]},{spot[1]} "
                                   f"({gained}/{self.args.logs_per_trip} logs)", spot)
                out, n = self.attempt(tree)
                if out in ("success", "fail"):
                    tries += 1
                    attempts += 1
                    self.memory.harvest_record(*node, out, n)
                    if out == "success":
                        successes += 1
                        gained += n
                        log(f"{label}: +{n} logs ({gained}/{self.args.logs_per_trip})")
                elif out == "depleted":
                    self.memory.harvest_record(*node, "depleted")
                    log(f"{label}: depleted")
                    break
                elif out == "lockout":
                    wait = n + self.human.rng.uniform(1.0, 3.0)
                    log(f"travel lockout reported: waiting {wait:.0f} s")
                    self.doing("lockout", f"Waiting out the travel lockout ({wait:.0f} s)", spot)
                    time.sleep(wait)
                    continue
                elif out == "not_tree":
                    self.memory.harvest_record(*node, "not_tree")
                    log(f"{label}: the server says this is not a tree; remembered")
                    break
                elif out == "none":
                    unknown += 1
                    log(f"{label}: no recognised outcome ({unknown})")
                    if unknown > 3:
                        raise Abort("harvest attempts keep ending without a known outcome")
                self.human.wait("between")
                self.human.fidget(self.link, self.link.state(), self.backpack(self.link.state()))
        self.stats.update(attempts=attempts, successes=successes, logs=gained)
        return gained

    # ------------------------------------------------------------ converting
    def convert(self):
        ok_text = self.k["convert"]["ok_text"]
        for _ in range(4):
            st = self.state()
            stacks = self.in_pack(st, LOGS)
            if not stacks:
                return
            serial, it = stacks[0]
            if "woods" not in self.stats:           # this trip's logs by wood type (ledger.py, woods.json)
                self.stats["woods"] = self.ledger.summary(kind="log")
            self.doing("convert", f"Making boards from {it.get('amount') or 1} logs")
            self.open_for((serial, False))              # the logs are targeted in the open backpack
            cur = self.use_hatchet()
            if cur is None:
                continue
            self.human.wait("aim")
            mark = len(self.link.events)
            self.ledger.expect(("consumed", serial))     # the log stack becomes boards: not theft
            self.link.act(actions.target_object(cur["cursor_id"], serial, it.get("x") or 0,
                                                it.get("y") or 0, 0, it["graphic"],
                                                cursor_type=cur["cursor_type"]))
            if self.link.wait(lambda s: self.heard(mark, text=ok_text), 5.0) is None:
                raise Abort(f"log stack 0x{serial:08X} did not convert")
            log(f"converted {it.get('amount') or 1} logs to boards")
            self.human.wait("between")
        raise Abort("logs left after 4 conversions")

    def open_for(self, *needs):
        """Open what the client must show first (agent_link.containers_to_open;
        needs are (serial, itself) pairs), like a player opening the bag."""
        world, todo = self.link.state()["world"], []
        for serial, itself in needs:
            todo += [s for s in containers_to_open(world, serial, itself) if s not in todo]
        if todo:
            self.link.open_containers(todo, self.human)

    # ------------------------------------------------------------ the room
    def room_menu(self, mark):
        room = self.k["room"]
        st = self.link.wait(lambda s: self.gump(mark, h(room["menu_gump_id"])), 5.0)
        if st is None:
            raise Abort("rental-room menu did not open")
        g = self.gump(mark, h(room["menu_gump_id"]))
        if ROOM_RENTED_BUTTON not in parse_layout(g.get("layout", ""))["buttons"]:
            raise Abort("the room menu offers no rented room (Test Shard wipe?); rent it again by hand")
        return g

    def tree_z_ok(self, tree):
        """Stand on the tree's level (not in a cave under it); map planner only."""
        if not self.mover.use_map:
            return None
        it = uomap.tiledata().item(h(tree["graphic"]))
        return reach_z(tree["z"], it.height if it else 0)

    def innkeeper_mobile(self):
        inn = self.k["npcs"]["innkeeper"]
        return self.link.state()["world"]["mobiles"].get(inn["serial"]) or {}

    def innkeeper_pos(self):
        """Where the innkeeper stands now (world model), else the demo position."""
        m = self.innkeeper_mobile()
        if m.get("x") is not None:
            return (m["x"], m["y"])
        return tuple(self.k["npcs"]["innkeeper"]["pos"][:2])

    def innkeeper_z(self) -> int:
        z = self.innkeeper_mobile().get("z")
        return z if z is not None else self.k["npcs"]["innkeeper"]["pos"][2]

    def enter_room(self):
        room, inn = self.k["room"], self.k["npcs"]["innkeeper"]
        # The room menu opens by speech from 13 tiles, but its buttons need the
        # vendor in range (11 tiles worked, 13 = "That vendor is too far away
        # from you.", live 2026-09-29): walk up to where the innkeeper is now.
        self.doing("to_inn", "Going home: heading to the innkeeper", self.innkeeper_pos())
        self.mover.walk_to(self.innkeeper_pos, self.args.inn_range, "to the innkeeper",
                           z_ok=same_floor(self.innkeeper_z()))
        self.doing("enter_room", "Asking the innkeeper to enter the rental room", self.innkeeper_pos())
        self.human.wait("speak")
        mark = len(self.link.events)
        self.link.act(actions.say_unicode("room"))
        g = self.room_menu(mark)
        self.human.wait("menu")
        mark = len(self.link.events)
        self.link.act(actions.gump_reply(g["serial"], g["gump_id"], room["enter_button"],
                                         g.get("layout", ""), g.get("lines") or []))
        texts = (room["enter_text"], room["too_far_text"])
        self.link.wait(lambda s: any(self.heard(mark, text=t) for t in texts), 5.0)
        if self.heard(mark, text=room["enter_text"]) is None:
            far = self.heard(mark, text=room["too_far_text"]) is not None
            raise Abort("did not enter the rental room" + (" (vendor too far away)" if far else ""))
        log("entered the rental room")
        self.link.wait(lambda s: s["movement"]["pos"] is not None
                       and cheb(s["movement"]["pos"], room["inside_pos"]) <= 12, 3.0)

    def store(self) -> int:
        room = self.k["room"]
        box = h(room["secure_container"]["serial"])
        st = self.link.wait(lambda s: self.item(s, box) is not None, 4.0)
        if st is None:
            raise Abort(f"secure container 0x{box:08X} not in the room")
        bpos = room["secure_container"]["pos"]
        self.doing("to_box", "Walking to the secure container", bpos[:2])
        self.mover.walk_to(lambda: bpos, 1, "to the secure container")
        stored = 0
        stacks = self.in_pack(self.state(), BOARDS)
        if stacks:  # boards are dragged from the open backpack into the open secure container
            self.open_for(*[(serial, False) for serial, _ in stacks], (box, True))
        for serial, it in stacks:
            amount = it.get("amount") or 1
            self.doing("store", f"Storing {amount} boards in the secure container", bpos[:2])
            self.human.wait("use")
            self.ledger.expect(("moved_out", serial))    # stored in the secure container: not theft
            self.link.act(actions.lift(serial, amount))
            self.human.wait("drag")
            self.link.act(actions.drop(serial, DROP_AUTO, DROP_AUTO, 0, 0, box))
            pack = self.backpack(self.link.state())
            moved = self.link.wait(
                lambda s: (self.item(s, serial) is None
                           or serial_of(self.item(s, serial).get("container") or "0") != pack), 4.0)
            if moved is None:
                raise Abort(f"board stack 0x{serial:08X} did not leave the backpack")
            stored += amount
            log(f"stored {amount} boards in the secure container")
        self.stats["stored"] = self.stats.get("stored", 0) + stored
        return stored

    def exit_room(self):
        room = self.k["room"]
        door = h(room["door"]["serial"])
        if self.link.wait(lambda s: self.item(s, door) is not None, 3.0) is None:
            raise Abort(f"room door 0x{door:08X} not known")
        self.doing("exit_room", "Leaving the rental room", room["door"]["pos"][:2])
        self.mover.walk_to(lambda: room["door"]["pos"][:2], 1, "to the door")
        self.human.wait("use")
        mark = len(self.link.events)
        self.link.act(actions.dclick(door))
        g = self.room_menu(mark)
        self.human.wait("menu")
        mark = len(self.link.events)
        self.link.act(actions.gump_reply(g["serial"], g["gump_id"], room["exit_button"],
                                         g.get("layout", ""), g.get("lines") or []))
        if self.link.wait(lambda s: self.heard(mark, text=room["exit_text"]), 5.0) is None:
            raise Abort("did not leave the rental room")
        self.t_exit = time.time()
        log("left the rental room")
        self.human.wait("read")
        self.human.fidget(self.link, self.link.state(), self.backpack(self.link.state()))

    def in_room(self, st) -> bool:
        return cheb(self.link.pos(st), self.k["room"]["inside_pos"]) <= 12

    # ------------------------------------------------------------ trips
    def episode(self, row):
        self.memory.episode("lumber", row)

    def trip(self, n):
        """One trip, ending in the safety of the rental room: (leave the room if
        we're in it) -> harvest -> convert -> enter the room -> store. The room
        is only left at the start of the next trip, so a run ends inside it."""
        self.stats = {}
        self.trip_n = n
        t0, s0, b0 = time.time(), self.mover.steps, self.mover.blocked_count
        phases = {}

        def timed(name, fn):
            t = time.time()
            r = fn()
            phases[name] = round(time.time() - t, 1)
            return r

        if self.in_room(self.state()):
            timed("exit", self.exit_room)
        timed("harvest", self.harvest_trip)
        timed("convert", self.convert)
        timed("to_room", self.enter_room)
        timed("store", self.store)
        row = {"loop": "lumber", "venue": self.k["venue"], "trip": n, "t_start": round(t0, 1),
               "t_end": round(time.time(), 1), "phases_s": phases,
               "steps": self.mover.steps - s0, "blocked": self.mover.blocked_count - b0,
               "doors_opened": self.mover.doors_opened,
               "human_session": dict(self.human.stats), **self.stats}
        self.episode(row)
        log(f"trip {n} done: {row}")
        self.doing("trip_done", f"Trip {n} done: {self.stats.get('logs', 0)} logs, "
                                f"{self.stats.get('stored', 0)} boards stored")

    def run(self):
        st = self.link.wait(lambda s: s["movement"]["pos"] is not None
                            and s["movement"]["self_serial"] is not None, 5.0)
        if st is None:
            raise Abort("proxy has no player position yet (log in first)")
        self.check_guards(st)
        self.hatchet(st)
        if self.in_room(st):
            log("starting inside the rental room")
            self.store()
        for n in range(1, self.args.trips + 1):
            self.trip(n)
        log(f"loop complete: {self.args.trips} trip(s); resting in the rental room")
        self.doing("done", f"Finished: {self.args.trips} trip(s); resting in the rental room")


def stop_intent(loop, text):
    """Last words for the visualizer; the proxy may already be gone."""
    try:
        loop.doing("stopped", text[:200])
    except (OSError, ValueError, Abort):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trips", type=int, default=1)
    ap.add_argument("--logs-per-trip", type=int, default=15)
    ap.add_argument("--max-attempts-per-tree", type=int, default=25)
    ap.add_argument("--max-trees", type=int, default=8, help="candidate trees tried per trip")
    ap.add_argument("--regrow-min", type=float, default=20.0,
                    help="skip a tree for this long after it was depleted")
    ap.add_argument("--attempt-timeout", type=float, default=10.0)
    ap.add_argument("--human", choices=sorted(PROFILES), default="normal",
                    help="human-texture profile (humanize.py); 'off' for deterministic tests")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--no-map", action="store_true",
                    help="plan on walk memory only (offline tests against simulated worlds)")
    ap.add_argument("--human-fast", type=float, default=1.0,
                    help="scale human delays (offline tests of the normal profile only)")
    ap.add_argument("--captcha-timeout", type=float, default=600.0)
    ap.add_argument("--captcha-max-strikes", type=int, default=2,
                    help="wrong auto-solve answers tolerated before the pause + beep fallback")
    ap.add_argument("--captcha-beep-s", type=float, default=30.0)
    ap.add_argument("--quiet", action="store_true", help="no handoff sound (tests)")
    ap.add_argument("--inn-range", type=int, default=4,
                    help="walk to within this many tiles of the innkeeper's current position")
    ap.add_argument("--timeout", type=float, default=3600.0)
    ap.add_argument("--max-blocked", type=int, default=20)
    ap.add_argument("--control-port", type=int, default=25941)
    ap.add_argument("--state-port", type=int, default=25942)
    ap.add_argument("--loop", default=os.path.join(DATA, "loops", "lumber.json"))
    ap.add_argument("--memory", default=DEFAULT_DB,
                    help="harness memory (SQLite, docs/MEMORY.md): walk memory, harvest nodes, episodes")
    args = ap.parse_args()

    with open(args.loop, encoding="utf-8") as f:
        know = json.load(f)
    memory = Memory(args.memory)
    link = Link(args.control_port, args.state_port)
    loop = LumberLoop(link, memory, know, args)
    code = 0
    try:
        loop.run()
    except Abort as e:
        log(f"ABORTED: {e}")
        code = 1
        stop_intent(loop, f"Stopped: {e}")
    except BaseException as e:
        stop_intent(loop, f"Crashed: {type(e).__name__}: {e}")
        raise
    finally:
        memory.close()
    sys.exit(code)


if __name__ == "__main__":
    main()

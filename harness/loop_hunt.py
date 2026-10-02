"""Hunt loop runner: fight monsters at one spot, heal, loot, leave when hurt (docs/HUNT_LOOP.md).

Built for New Player Dungeon mongbats (user decision 2026-10-01: thresholds are task
arguments). The character stands on --spot (the NPD exit tile by default) and fights
what comes; leaving is war mode off, back to the spot, one step in --exit-dir onto
the exit teleporter.

Targets come only from the live world model (world.mobiles holds what the stock
client still has: dead and out-of-range mobiles are pruned, docs/WORLDMODEL.md) and
pass the same monsters-only guard as `ctl act attack` (combat.attackable:
threats.identify monster, notoriety 3-6, on screen). A mob swinging at us
(world.swings) comes first, the one with the lowest hits; otherwise the nearest
within --pull-range of the spot. The state is re-read right before every cast and
every target answer; a target that is no longer live is never targeted (the cursor
is cancelled with the stock Esc packet instead; ANTICHEAT.md §10 A12).

Packets are the stock client's (combat.py): war mode on (0x72), 0x34 unless a status
request is outstanding, 0x05; spells are 0xFF sub 4 then 0x6C; loot is 0x06 on the
corpse, then 0x07 lift + 0x08 drop per item, gold first; war mode off when nothing
is near. Pacing comes from humanize.Human.

Rules (all CLI arguments): heal below --heal-at (healing.py: a heal potion whenever
one can be drunk, else Heal or Greater Heal by the missing hits, Greater Heal from
--gheal-min-missing); the attack spell while mana >= --mana-reserve + its cost;
leave below --leave-at, or below --leave-multi-at with two or more attackers, or
when a hostile player comes close. Outside, rest (Heal / Greater Heal, no potions,
regeneration) to --rest-to and go back in (--rest-to 0: stop after leaving). The
run ends outside (an idle character in the NPD gets killed).

Guards: overall timeout, movement stall, the agent gate (Link.act waits it out),
server restriction text, death (`death` juncture, stop; no corpse runs), a character
speaking nearby (speech_guard.py: `speech_nearby` hold, deferred until no fight is
on; leaving to survive overrides the hold). Junctures: `threat` when leaving,
`low_supplies` when neither a potion nor the mana for Heal is there, `death`. Job events and one episode
row per visit (kills, gold, xp, hits lost) go to the memory store.

Run:  python harness/loop_hunt.py [--kills 5] [--enter] [--spell lightning]
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions  # noqa: E402
import alerts  # noqa: E402
import combat  # noqa: E402
import healing  # noqa: E402
import threats  # noqa: E402
import triage  # noqa: E402
from agent_link import Abort, Link, Mover, cheb, containers_to_open, log, serial_of  # noqa: E402
from errand_bank import GATING_WORDS  # noqa: E402
from humanize import PROFILES, Human  # noqa: E402
from memory import DEFAULT_DB, Memory  # noqa: E402
from speech_guard import SpeechGuard, staff_hints  # noqa: E402

SWING_RECENT_S = 10.0         # a mob whose last swing at us is this recent is attacking us
CAST_CURSOR_WAIT_S = 4.0      # a spell's cursor comes after its cast delay
SPELL_RANGE = 10              # [INFERENCE] RunUO spell range (12 pre-ML, 10 ML)
SPELL_BLOCK_S = 10.0          # after "too far away" / "cannot be seen": melee only for this long
NO_LOS_CLILOCS = (500446, 500237)   # "That is too far away." / "Target cannot be seen."
CORPSE_WAIT_S = 4.0           # a dead mob's corpse item shows up within this
CONTAINER_WAIT_S = 3.0
POLL_S = 0.4
TELEPORT_TRIES = 3            # turn, step (+ one retry) onto a teleporter
TELEPORT_WAIT_S = 1.0         # a confirmed step onto a teleporter: the move follows within this
HARD_TIMEOUT_GRACE_S = 180.0  # past --timeout the runner leaves; past this too it aborts
GOTO_Z_TOL = 10               # ctl.GOTO_Z_TOL


def key_of(serial: int) -> str:
    return f"0x{serial:08X}"


class HuntLoop:
    def __init__(self, link: Link, memory: Memory, args):
        self.link = link
        self.memory = memory
        self.args = args
        self.t0 = time.monotonic()
        self.human = Human(args.human, seed=args.seed, fast=args.human_fast, log=log)
        self.mover = Mover(link, memory, self.human, max_blocked=args.max_blocked,
                           guard=self.check_guards, doors=True, use_map=not args.no_map)
        self.spot = tuple(args.spot)
        self.spell = combat.spell_id(args.spell)
        if self.spell is None:
            raise Abort(f"unknown spell {args.spell!r}")
        self.words = args.target_name.lower().split()
        self.watch = threats.Watch(threats.Params(flee_on_attack=False))
        self.speech = SpeechGuard()
        self.triage = triage.Triage(args.triage_url, log=log)
        self.pending_speech = []
        self.heard_upto = None       # restriction-text scan cursor into link.events
        self.ev_upto = 0             # kill/death scan cursor into link.events
        self.dead = set()            # serials the server reported dead (0xFF sub 0xDEAD)
        self.corpse_of = {}          # dead mobile serial -> its corpse serial (0xDEAD)
        self.corpses = []            # [{mob, name, x, y, t}] kills to loot
        self.looted = set()          # corpse serials handled (looted or refused)
        self.engaged = None          # serial we last sent 0x05 for
        self.engaged_mob = None      # its latest world dict
        self.resent_adjacent = False
        self.spell_block = {}        # serial -> monotonic time until spells at it are skipped
        self.prev_hits = None
        self.low_posted = False
        self.potions = healing.PotionClock()
        self.visit_n = 0
        self.visit = None            # this visit's counters (episode row)
        self.totals = {"kills": 0, "gold": 0, "xp": 0, "hits_lost": 0, "casts": 0, "heals": 0, "potions": 0,
                       "leaves": 0, "visits": 0}
        self._intent = None
        self.left_why = None         # (why, severity) of the last leave

    # ------------------------------------------------------------ reporting
    def doing(self, kind: str, text: str, target=None, serial=None):
        """Tell the visualizer what the agent is trying to do (proxy-side only)."""
        self._intent = (kind, text, target, serial)
        self.link.intent(text, kind, target, loop="hunt",
                         target_serial=key_of(serial) if serial is not None else None)

    def _where(self, st):
        pos = st["movement"]["pos"] or [None, None]
        return {"facet": st["world"]["self"].get("map"), "x": pos[0], "y": pos[1]}

    def count(self, what, n=1):
        self.totals[what] += n
        if self.visit is not None:
            self.visit[what] = self.visit.get(what, 0) + n

    # ------------------------------------------------------------ guards
    def check_guards(self, st: dict):
        if time.monotonic() - self.t0 > self.args.timeout + HARD_TIMEOUT_GRACE_S:
            raise Abort(f"overall timeout ({self.args.timeout:.0f}s + {HARD_TIMEOUT_GRACE_S:.0f}s to leave)")
        mv = st["movement"]
        if mv["stalled"]:
            raise Abort(f"movement stalled ({mv['rejects_in_row']} walks rejected in a row)")
        if threats.is_dead(st) or st["world"]["self"].get("dead"):
            self.died(st, "ghost body")
        self.check_restriction()
        self.pending_speech += self.new_speakers(st)

    def check_restriction(self):
        """Server text restricting assistants (ANTICHEAT.md §8 rule 4): stop."""
        if self.heard_upto is None:
            self.heard_upto = len(self.link.events)
        for ev in self.link.events[self.heard_upto:]:
            if ev.get("ev") == "speech_heard" and ev.get("serial") in (None, 0, 0xFFFFFFFF, "0xFFFFFFFF"):
                text = (ev.get("text") or "").lower()
                if any(w in text for w in GATING_WORDS):
                    self.memory.juncture("hunt", "server_restriction",
                                         f"Server restriction: {ev.get('text')!r}"[:300], "urgent",
                                         {"text": ev.get("text")})
                    raise Abort(f"server restriction message: {ev.get('text')!r}")
        self.heard_upto = len(self.link.events)

    def new_speakers(self, st):
        who = self.speech.scan(st["world"], self.link.events, self.link.event_t)
        for w in who:
            v = self.triage.judge(w, st["world"], names=self.speech.names)
            if v and "error" not in v:
                log(f"laya: {w['label'] or w['name'] or w['serial']}: {w['text']!r} "
                    f"check {v['check']:.2f} direct {v['direct']:.2f} ({v['ms']} ms)")
        return who

    def died(self, st, reason):
        a = self.watch.update(st, recall_s=0.0, margin_s=0.0)
        players = [t for t in a.threats if t.hostile and t.player]
        cause = "pk" if players else ("mob" if self.attackers(st) or a.under_attack else "unknown")
        data = {"reason": reason, "cause": cause, "attackers": self.attackers(st), "threats": a.to_dict()}
        self.memory.juncture("hunt", "death", f"Died ({cause}): {reason}", "urgent", data)
        self.memory.job_event("hunt", "death", data, **self._where(st))
        raise Abort(f"died ({cause}): {reason}")

    def state(self):
        st = self.link.state()
        self.check_guards(st)
        self.scan_events(st)
        hits = st["world"]["self"].get("hits")
        if hits is not None:
            if self.prev_hits is not None and hits < self.prev_hits:
                self.count("hits_lost", self.prev_hits - hits)
            self.prev_hits = hits
        return st

    # ------------------------------------------------------------ world lookups
    def me(self, st) -> int:
        return st["movement"]["self_serial"]

    def pos(self, st):
        return self.link.pos(st)

    def frac(self, d: dict, cur="hits", top="hits_max") -> float:
        c, m = d.get(cur), d.get(top)
        return c / m if c is not None and m else 1.0

    def at_hunt(self, st) -> bool:
        return cheb(self.pos(st), self.spot) <= combat.VIEW_RANGE

    def wanted(self, world, key, mob) -> bool:
        text = ((world.get("labels") or {}).get(key) or mob.get("name") or "").lower()
        return bool(text) and all(w in text for w in self.words)

    def live_target(self, st, serial, spell_range=None):
        """(mob, why not): `serial` is live in the world model, not reported dead,
        passes the attack guard and is on screen (within spell_range if given)."""
        world, key = st["world"], key_of(serial)
        mob = world["mobiles"].get(key)
        if mob is None or serial in self.dead:
            return None, "no longer in the client's world"
        ok, why = combat.attackable(world, key)
        if not ok:
            return None, why
        d = cheb(self.pos(st), (mob["x"], mob["y"]))
        if d > (spell_range if spell_range is not None else combat.VIEW_RANGE):
            return None, f"{d} tiles away"
        return mob, ""

    def attackers(self, st) -> list[dict]:
        """Live mobiles whose latest swing (world.swings) hit at us within SWING_RECENT_S."""
        world, me = st["world"], key_of(self.me(st))
        now, pos, out = time.time(), self.pos(st), []
        for att, sw in (world.get("swings") or {}).items():
            mob = world["mobiles"].get(att)
            if mob is None or mob.get("x") is None or serial_of(att) in self.dead:
                continue
            if serial_of(sw.get("defender")) != serial_of(me) or now - sw.get("t", 0) > SWING_RECENT_S:
                continue
            out.append({"serial": att, "name": (world.get("labels") or {}).get(att) or mob.get("name"),
                        "dist": cheb(pos, (mob["x"], mob["y"])), "hits": [mob.get("hits"), mob.get("hits_max")],
                        "last_swing_age_s": round(now - sw.get("t", 0), 1)})
        return sorted(out, key=lambda a: a["dist"])

    def pick_target(self, st, pull: bool = True):
        """The serial to fight now, or None: keep the current one while it's valid and
        nothing else is attacking us; else an attacker with the lowest hits; else (pull)
        the nearest within --pull-range of the spot."""
        world, me, pos = st["world"], self.me(st), self.pos(st)
        attacking = {serial_of(a["serial"]) for a in self.attackers(st)}
        cands = []
        for key, mob in world["mobiles"].items():
            s = serial_of(key)
            if s == me or mob.get("x") is None or not self.wanted(world, key, mob):
                continue
            if self.live_target(st, s)[0] is None:
                continue
            cands.append((s, mob))
        if pull and self.engaged is not None and any(s == self.engaged for s, _ in cands) \
                and (self.engaged in attacking or not attacking):
            return self.engaged
        att = [(s, m) for s, m in cands if s in attacking]
        if att:
            if any(s == self.engaged for s, _ in att):
                return self.engaged
            return min(att, key=lambda sm: (self.frac(sm[1]), cheb(pos, (sm[1]["x"], sm[1]["y"]))))[0]
        near = [(s, m) for s, m in cands if cheb(self.spot, (m["x"], m["y"])) <= self.args.pull_range]
        if pull and near:
            return min(near, key=lambda sm: cheb(pos, (sm[1]["x"], sm[1]["y"])))[0]
        return None

    def backpack(self, st) -> int:
        pack = combat.backpack(st["world"]["items"], self.me(st))
        if pack is None:
            raise Abort("backpack not known to the world model")
        return pack

    def pack_gold(self, st) -> int:
        """Gold in the backpack at any bag depth (piles merge, so serials change)."""
        items, pack = st["world"]["items"], self.backpack(st)
        total = 0
        for it in items.values():
            if it.get("graphic") != combat.GOLD_GRAPHIC:
                continue
            c, depth = it.get("container"), 0
            while c is not None and serial_of(c) != pack and depth < 8:
                c, depth = (items.get(key_of(serial_of(c))) or {}).get("container"), depth + 1
            if c is not None and serial_of(c) == pack:
                total += it.get("amount") or 1
        return total

    # ------------------------------------------------------------ events
    def scan_events(self, st):
        """Deaths, and our target leaving the world model. On Outlands an in-view kill is
        S2C 0xAF + 0x1D + 0xFF sub 0xDEAD at once (capture 20261001_214649): `prune`
        (why dead) or `delete` of our target is the kill, and `mobile_death` names its
        corpse, in either order. A serial in a mobile_death is never targeted again."""
        for ev in self.link.events[self.ev_upto:]:
            kind = ev.get("ev")
            if kind == "mobile_death":
                s = serial_of(ev["serial"])
                self.dead.add(s)
                if ev.get("corpse") is not None:
                    self.corpse_of[s] = serial_of(ev["corpse"])
                if s == self.engaged:
                    self.on_kill(st, s)
            elif kind in ("delete", "prune") and self.engaged is not None \
                    and serial_of(ev["serial"]) == self.engaged:
                why = ev.get("why", "delete")
                if why in ("delete", "dead"):
                    self.on_kill(st, self.engaged)
                else:
                    log(f"target 0x{self.engaged:08X} left the client's world ({why}); not a kill")
                    self.disengage()
        self.ev_upto = len(self.link.events)

    def on_kill(self, st, serial):
        mob = self.engaged_mob or {}
        name = mob.get("name") or "a monster"
        self.count("kills")
        log(f"killed {name} 0x{serial:08X} (kill {self.totals['kills']}"
            f"{'/' + str(self.args.kills) if self.args.kills else ''})")
        self.memory.job_event("hunt", "kill", {"serial": key_of(serial), "name": name,
                                               "hits_max": mob.get("hits_max"), "visit": self.visit_n},
                              **self._where(st))
        if self.args.loot:
            self.corpses.append({"mob": serial, "name": name, "x": mob.get("x"), "y": mob.get("y"),
                                 "t": time.monotonic()})
        self.disengage()

    def disengage(self):
        self.engaged, self.engaged_mob, self.resent_adjacent = None, None, False

    # ------------------------------------------------------------ actions
    def war_mode(self, st, on: bool) -> bool:
        """The stock Tab toggle: nothing is sent when war mode already is `on`."""
        if bool(st["world"]["self"].get("warmode")) == on:
            return False
        self.link.act(actions.war_mode(on))
        self.link.wait(lambda s: bool(s["world"]["self"].get("warmode")) == on, 3.0)
        return True

    def attack(self, serial) -> bool:
        st = self.state()
        mob, why = self.live_target(st, serial)
        if mob is None:
            log(f"not attacking 0x{serial:08X}: {why}")
            return False
        if self.war_mode(st, True):
            self.human.wait("use")
            st = self.state()
            mob, why = self.live_target(st, serial)
            if mob is None:
                log(f"not attacking 0x{serial:08X}: {why}")
                return False
        name = mob.get("name") or key_of(serial)
        self.doing("attack", f"Attacking {name}", (mob["x"], mob["y"]), serial)
        for pkt in combat.attack_packets(st["world"], serial):
            self.link.act(pkt)
        if self.engaged != serial:
            log(f"attacking {name} 0x{serial:08X} at {cheb(self.pos(st), (mob['x'], mob['y']))} tiles "
                f"(hits {mob.get('hits')}/{mob.get('hits_max')})")
        self.engaged, self.engaged_mob = serial, mob
        return True

    def await_cursor(self, mark):
        """The spell's target cursor (world.target) once a `target` event follows
        `mark`; None on a cliloc instead (fizzle, no mana, not recovered) or timeout."""
        end = time.monotonic() + CAST_CURSOR_WAIT_S
        while time.monotonic() < end:
            st = self.state()
            evs = self.link.events[mark:]
            cur = st["world"].get("target") or {}
            if any(e.get("ev") == "target" for e in evs) and cur.get("active") and cur.get("cursor_id") is not None:
                return cur
            heard = [e.get("cliloc") for e in evs if e.get("ev") == "cliloc"]
            if heard:
                log(f"cast answered with cliloc {heard}")
                return None
            time.sleep(0.1)
        return None

    def cast(self, sid, what: str):
        """0xFF sub 4; the target cursor or None."""
        mark = len(self.link.events)
        self.link.act(actions.cast_spell(sid))
        self.count("casts")
        cur = self.await_cursor(mark)
        if cur is None:
            log(f"{combat.MAGERY_SPELLS[sid - 1]} at {what}: no target cursor")
        return cur

    def cancel(self, cur):
        self.link.act(actions.target_cancel(cur["cursor_id"], cur.get("target_type") or 0, cur.get("cursor_type") or 0))

    def cursor_still(self, st, cur) -> bool:
        now = st["world"].get("target") or {}
        return bool(now.get("active")) and now.get("cursor_id") == cur["cursor_id"]

    def cast_at(self, serial) -> bool | None:
        """The attack spell at `serial`: state re-read right before the cast and
        right before the target answer; a target gone meanwhile gets the cursor
        cancelled, never a 0x6C at it. True when cast; None when dropped because the
        leave rule fired while aiming (the caller leaves at once); else False."""
        st = self.state()
        mob, why = self.live_target(st, serial, SPELL_RANGE)
        if mob is None:
            return False
        name = mob.get("name") or key_of(serial)
        self.doing("cast", f"Casting {combat.MAGERY_SPELLS[self.spell - 1]} at {name}", (mob["x"], mob["y"]), serial)
        cur = self.cast(self.spell, name)
        if cur is None:
            return False
        self.human.wait("aim")
        st = self.state()
        mob, why = self.live_target(st, serial, SPELL_RANGE)
        if not self.cursor_still(st, cur):
            log("the spell's cursor went away before the target answer")
            return False
        if mob is None:
            log(f"target 0x{serial:08X} {why} before the spell landed: cancelling the cursor")
            self.cancel(cur)
            return False
        if self.leave_reason(st):
            log("the leave rule fired while aiming: cancelling the cursor")
            self.cancel(cur)
            return None
        mark = len(self.link.events)
        self.link.act(combat.target_mobile(cur, serial, mob))
        got = self.link.wait(lambda s: any(e.get("ev") == "cliloc" or (e.get("ev") == "damage"
                                                                      and serial_of(e["serial"]) == serial)
                                           for e in self.link.events[mark:]), 1.5)
        if got is not None:
            heard = [e.get("cliloc") for e in self.link.events[mark:] if e.get("ev") == "cliloc"]
            if any(c in NO_LOS_CLILOCS for c in heard):
                log(f"spell at 0x{serial:08X}: cliloc {heard}; melee only for {SPELL_BLOCK_S:.0f} s")
                self.spell_block[serial] = time.monotonic() + SPELL_BLOCK_S
        return True

    def heal(self, st, potions: bool = True) -> bool:
        """One heal on self by healing.choose: a heal potion whenever one can be drunk
        (`potions`), else Heal or Greater Heal by the missing hits. When neither is
        possible, one low_supplies juncture per visit."""
        world, me = st["world"], st["world"]["self"]
        ready = potions and self.potions.ready(time.monotonic())
        choice = healing.choose(world, self.me(st), ready, self.args.gheal_min_missing)
        if choice.kind == "potion":
            if self.drink(st, serial_of(choice.potion)):
                return True
            st = self.state()
            world, me = st["world"], st["world"]["self"]
            choice = healing.choose(world, self.me(st), False, self.args.gheal_min_missing)
        if choice.kind == "spell":
            return self.heal_spell(st, choice.spell)
        if choice.missing > 0 and not self.low_posted:
            self.low_posted = True
            data = {"item": "heal", "have": {"mana": me.get("mana"), "potions": len(healing.heal_potions(world, self.me(st)))},
                    "need": {"mana": combat.spell_mana(healing.HEAL)},
                    "hits": [me.get("hits"), me.get("hits_max")], "why": choice.why}
            self.memory.juncture("hunt", "low_supplies",
                                 f"No heal possible at {me.get('hits')}/{me.get('hits_max')} hits: {choice.why}",
                                 "attention", data)
            log(f"low supplies: {choice.why}")
        return False

    def drink(self, st, serial: int) -> bool:
        """Drink the heal potion `serial` like `ctl act use` (open its containers first,
        stock double-click). True when it healed; a 'wait' refusal (cliloc 500235)
        restarts the potion clock and returns False so the caller casts instead."""
        me = st["world"]["self"]
        self.doing("heal", f"Drinking a heal potion ({me.get('hits')}/{me.get('hits_max')} hits)")
        self.open_for((serial, False))
        self.human.wait("use")
        mark = len(self.link.events)
        self.link.act(actions.dclick(serial))
        answers = (healing.CLILOC_HEALED, healing.CLILOC_POTION_WAIT, healing.CLILOC_FULL_HEALTH)
        self.link.wait(lambda s: any(e.get("ev") == "cliloc" and e.get("cliloc") in answers
                                     for e in self.link.events[mark:]), 2.0)
        heard = {e.get("cliloc") for e in self.link.events[mark:] if e.get("ev") == "cliloc"}
        self.potions.started(time.monotonic())
        hits = self.link.last["world"]["self"].get("hits")
        if healing.CLILOC_POTION_WAIT in heard:
            log(f"heal potion refused: still cooling down ({me.get('hits')}/{me.get('hits_max')} hits)")
            return False
        self.count("potions")
        log(f"heal potion: {me.get('hits')} -> {hits} hits")
        return healing.CLILOC_HEALED in heard or (hits or 0) > (me.get("hits") or 0)

    def heal_spell(self, st, sid: int) -> bool:
        """Heal or Greater Heal on self: cast, then answer the cursor with self."""
        me = st["world"]["self"]
        name = combat.MAGERY_SPELLS[sid - 1]
        self.doing("heal", f"Casting {name} on myself ({me.get('hits')}/{me.get('hits_max')} hits)")
        cur = self.cast(sid, "myself")
        if cur is None:
            return False
        self.human.wait("aim")
        st = self.state()
        if not self.cursor_still(st, cur):
            return False
        self.link.act(combat.target_self(cur, self.me(st), self.pos(st), st["world"]["self"].get("body")))
        self.count("heals")
        self.link.wait(lambda s: (s["world"]["self"].get("hits") or 0) > (me.get("hits") or 0), 2.0)
        log(f"{name.lower()}: {me.get('hits')} -> {self.link.last['world']['self'].get('hits')} hits")
        return True

    def open_for(self, *needs):
        """Open what the client must show first (containers_to_open), like a player."""
        world, todo = self.link.state()["world"], []
        for serial, itself in needs:
            todo += [s for s in containers_to_open(world, serial, itself) if s not in todo]
        if todo:
            self.link.open_containers(todo, self.human)

    def loot(self, c) -> None:
        """Loot one corpse like `ctl act loot`: refuse human corpses, walk within
        LOOT_RANGE, open the backpack and the corpse, then lift + drop each item into
        the backpack, gold first, stopping at the weight limit."""
        st = self.state()
        if c.get("corpse") is None:  # 0xDEAD names it; else the corpse that appeared on its tile
            c["corpse"] = self.corpse_of.get(c["mob"]) or next(
                (serial_of(k) for k, it in st["world"]["items"].items()
                 if it.get("graphic") == combat.CORPSE_GRAPHIC and it.get("container") is None
                 and serial_of(k) not in self.looted
                 and c["x"] is not None and cheb((it["x"], it["y"]), (c["x"], c["y"])) <= 1), None)
        corpse = st["world"]["items"].get(key_of(c["corpse"])) if c["corpse"] is not None else None
        if corpse is None:
            if time.monotonic() - c["t"] > CORPSE_WAIT_S:
                log(f"no corpse seen for {c['name']}; nothing to loot")
                self.corpses.remove(c)
            else:
                time.sleep(POLL_S)
            return
        self.corpses.remove(c)
        self.looted.add(c["corpse"])
        if corpse.get("graphic") != combat.CORPSE_GRAPHIC or corpse.get("container") is not None:
            log(f"0x{c['corpse']:08X} isn't a corpse on the ground; not looting")
            return
        if combat.human_corpse(corpse):
            log(f"0x{c['corpse']:08X} ({corpse.get('name')}) is a human corpse: not looted")
            return
        spot = (corpse["x"], corpse["y"])
        if cheb(self.spot, spot) > self.args.pull_range + combat.LOOT_RANGE:
            log(f"corpse at {spot} is too far from the spot; leaving it")
            return
        self.doing("loot", f"Looting {corpse.get('name') or c['name']}", spot, c["corpse"])
        if cheb(self.pos(st), spot) > combat.LOOT_RANGE:
            self.mover.walk_to(lambda: spot, 1, "to the corpse")
            st = self.state()
        pack = self.backpack(st)
        gold0, sgold0 = self.pack_gold(st), st["world"]["self"].get("gold")
        self.open_for((pack, True))
        self.human.wait("use")
        self.link.act(actions.dclick(c["corpse"]))
        st = self.link.wait(lambda s: combat.corpse_contents(s["world"], c["corpse"]), CONTAINER_WAIT_S)
        inside = combat.corpse_contents(st["world"], c["corpse"]) if st else {}
        # Mastery-chain XP of a kill = the creature's gold value x our damage share
        # (wiki Experience_Gain); solo, the gold the corpse holds is that value [INFERENCE].
        xp = sum(it.get("amount") or 1 for it in inside.values() if it.get("graphic") == combat.GOLD_GRAPHIC)
        self.count("xp", xp)
        taken = []
        for k, it in combat.loot_order(inside)[: self.args.loot_max]:
            me = self.state()["world"]["self"]
            if me.get("weight") is not None and me.get("weight_max") and me["weight"] >= me["weight_max"]:
                log(f"weight {me['weight']}/{me['weight_max']}: leaving the rest")
                break
            self.human.wait("drag")
            s = serial_of(k)
            for pkt in combat.grab_packets(s, it.get("amount") or 1, pack):
                self.link.act(pkt)
            moved = self.link.wait(lambda st2: (st2["world"]["items"].get(k) is None or serial_of(
                st2["world"]["items"][k].get("container") or "0") == pack), CONTAINER_WAIT_S)
            if moved is not None:
                taken.append({"serial": k, "graphic": f"0x{it.get('graphic') or 0:04X}", "amount": it.get("amount")})
        st = self.state()
        gained = max(self.pack_gold(st) - gold0,
                     (st["world"]["self"].get("gold") or 0) - (sgold0 or 0) if sgold0 is not None else 0)
        self.count("gold", gained)
        log(f"looted {len(taken)} item(s) from {corpse.get('name') or c['name']}: +{gained} gold, ~{xp} xp")
        self.memory.job_event("hunt", "loot", {"corpse": key_of(c["corpse"]), "mob": key_of(c["mob"]),
                                               "name": c["name"], "gold": gained, "xp": xp, "items": taken,
                                               "visit": self.visit_n}, **self._where(st))

    # ------------------------------------------------------------ leaving / entering
    def leave_reason(self, st):
        """(why, severity) when the rules say leave now, else None: a hostile player
        within spell range (urgent: no going back in), two or more attackers below
        --leave-multi-at, below --leave-at."""
        me = st["world"]["self"]
        a = self.watch.update(st, recall_s=0.0, margin_s=0.0)
        pk = [t for t in a.threats
              if t.player and t.hostile and 0 <= t.distance <= self.watch.params.player_strike_range]
        if pk:
            t = pk[0]
            return f"hostile player {t.name or hex(t.serial)} ({t.kind}) at {t.distance} tiles", "urgent"
        if me.get("hits") is None:
            return None
        f, att = self.frac(me), self.attackers(st)
        if len(att) >= 2 and f < self.args.leave_multi_at:
            return (f"{len(att)} attackers and hits {me['hits']}/{me['hits_max']} below "
                    f"{self.args.leave_multi_at:.0%}"), "attention"
        if f < self.args.leave_at:
            return f"hits {me['hits']}/{me['hits_max']} below {self.args.leave_at:.0%}", "attention"
        return None

    def leave(self, st, why, severity):
        """War mode off, back to the spot, the step onto the exit teleporter. A
        `threat` juncture unless it's the planned end (severity info)."""
        self.left_why = (why, severity)
        data = {"why": why, "hits": [st["world"]["self"].get("hits"), st["world"]["self"].get("hits_max")],
                "attackers": self.attackers(st), "visit": self.visit_n, "kills": self.totals["kills"]}
        if severity != "info":
            self.memory.juncture("hunt", "threat", f"Leaving the hunt: {why}", severity, data)
        self.memory.job_event("hunt", "leave", data, **self._where(st))
        log(f"LEAVING: {why}")
        self.count("leaves")
        self.disengage()
        self.doing("leave", f"Leaving: {why}", self.spot)
        if self.war_mode(st, False):
            self.human.wait("read")
        self.mover.walk_to(lambda: self.spot, 0, "to the exit")
        self.teleport(self.args.exit_dir, "the exit")
        self.end_visit(why)

    def teleport(self, d, what):
        """Step in direction d onto a teleporter (a turn first if not facing it). Some
        deny the step and then move you (the NPD exit, live 2026-09-30), others may
        confirm it and move you right after."""
        for _ in range(TELEPORT_TRIES):
            self.mover.pace(True)
            out = self.mover.step(d, run=True)
            if out == "moved":
                here = tuple(self.pos(self.link.state())[:2])
                if self.link.wait(lambda s: cheb(self.pos(s), here) > 1, TELEPORT_WAIT_S) is not None:
                    out = "teleported"
            if out == "teleported":
                log(f"{what}: teleported to {self.pos(self.link.state())[:3]}")
                return
            if out == "moved":
                raise Abort(f"{what}: the step in direction {d} moved without a teleport")
        raise Abort(f"{what}: no teleport after {TELEPORT_TRIES} steps in direction {d}")

    def enter(self):
        ex, ey, ez = self.args.entry
        self.doing("enter", f"Going to the entrance at {ex},{ey}", (ex, ey))
        self.mover.walk_to(lambda: (ex, ey), 0, "to the entrance", z_ok=lambda z: abs(z - ez) <= GOTO_Z_TOL)
        self.teleport(self.args.entry_dir, "the entrance")
        st = self.state()
        if not self.at_hunt(st):
            raise Abort(f"the entrance put us at {self.pos(st)[:2]}, not near the spot {self.spot}")

    def rest(self) -> bool:
        """Outside: heal with Heal / Greater Heal (healing.choose without potions: out of
        the fight regenerating mana is free, potions cost gold) and regenerate to
        --rest-to hits and --mana-reserve mana. False when that takes longer than
        --rest-timeout."""
        end = time.monotonic() + self.args.rest_timeout
        while True:
            st = self.state()
            me = st["world"]["self"]
            need_mana = min(self.args.mana_reserve, me.get("mana_max") or 0)
            if self.frac(me) >= self.args.rest_to and (me.get("mana") or 0) >= need_mana:
                log(f"rested: hits {me.get('hits')}/{me.get('hits_max')}, mana {me.get('mana')}")
                return True
            if time.monotonic() > end:
                log(f"rest: still {me.get('hits')}/{me.get('hits_max')} hits, mana {me.get('mana')} "
                    f"after {self.args.rest_timeout:.0f} s")
                return False
            if self.pending_speech:
                self.speech_hold(st)
                continue
            if self.frac(me) < self.args.rest_to and healing.choose(
                    st["world"], self.me(st), False, self.args.gheal_min_missing).kind == "spell":
                self.heal(st, potions=False)
                self.human.wait("read")
                continue
            self.doing("rest", f"Resting ({me.get('hits')}/{me.get('hits_max')} hits, mana {me.get('mana')})")
            time.sleep(2.0)

    # ------------------------------------------------------------ speech hold
    def speech_hold(self, st):
        """Send nothing until the overseer acks `speech_nearby` and no gm_suspected
        is open (speech_guard.py), as loop_lumber does. Leaving to survive overrides
        the hold (a held character in the NPD dies)."""
        who, self.pending_speech = self.pending_speech, []
        first = who[0]
        name = first["label"] or first["name"] or first["serial"]
        log(f"SPEECH: {name}: {first['text']!r}; pausing for the overseer")
        resume = self._intent
        self.doing("speech", f"Paused: {name} spoke nearby; waiting for the overseer")
        data = {"hold": True, "task": "hunt", "visit": self.visit_n, "speakers": who, **self._where(st)}
        jid = self.memory.juncture("hunt", "speech_nearby",
                                   f"{name} said {first['text']!r} nearby; hunting paused until the "
                                   f"all-clear (ack)"[:300], "urgent", data)
        self.memory.job_event("hunt", "speech_hold", data, **self._where(st))
        if not self.suspect_staff(who, st):
            alerts.handoff(not self.args.quiet)
        t0, heard, lines = time.monotonic(), {w["serial"] for w in who}, list(who)
        while True:
            time.sleep(1.0)
            st = self.state()
            new, self.pending_speech = self.pending_speech, []
            for w in new:
                log(f"SPEECH (paused): {w['label'] or w['name'] or w['serial']}: {w['text']!r}")
                heard.add(w["serial"])
            lines += new
            self.suspect_staff(new, st)
            alerts.staff_alarm_due(self.memory, not self.args.quiet)
            if self.visit is not None and self.at_hunt(st):
                why = self.leave_reason(st)
                if why:
                    log("leaving despite the speech hold (survival)")
                    self.leave(st, *why)
            j = self.memory.junctures(after_id=jid - 1, limit=1)
            if j and j[0]["acked_t"] is not None and not alerts.open_gm(self.memory):
                break
        waited = time.monotonic() - t0
        self.t0 += waited                        # the pause isn't the job's time
        self.memory.job_event("hunt", "speech_clear", {"juncture": jid, "waited_s": round(waited, 1),
                                                       "lines": lines}, **self._where(st))
        self.speech.clear(heard)
        log(f"all-clear after {waited:.0f} s; resuming")
        self.human.wait("read")
        if resume is not None:
            self.doing(*resume)

    def suspect_staff(self, who, st) -> bool:
        hinted = [w for w in who if staff_hints(w)]
        if hinted and not alerts.open_gm(self.memory):
            w = hinted[0]
            name = w["label"] or w["name"] or w["serial"]
            log(f"POSSIBLE STAFF: {name} ({', '.join(staff_hints(w))}); staff alarm")
            alerts.post_gm(self.memory, "hunt", f"{name}: {', '.join(staff_hints(w))}",
                           {"task": "hunt", "speakers": hinted, **self._where(st)}, not self.args.quiet)
        return bool(alerts.open_gm(self.memory))

    # ------------------------------------------------------------ visits
    def start_visit(self, st):
        self.visit_n += 1
        self.count("visits")
        self.low_posted = False
        self.visit = {"loop": "hunt", "visit": self.visit_n, "t_start": round(time.time(), 1),
                      "spot": list(self.spot), "spell": combat.MAGERY_SPELLS[self.spell - 1],
                      "hits_start": st["world"]["self"].get("hits"), "kills": 0, "gold": 0, "xp": 0,
                      "hits_lost": 0, "casts": 0, "heals": 0}

    def end_visit(self, why):
        if self.visit is None:
            return
        row = {**self.visit, "t_end": round(time.time(), 1), "ended": why,
               "human_session": dict(self.human.stats)}
        self.memory.episode("hunt", row)
        log(f"visit {self.visit_n} done: {row}")
        self.visit = None

    def finished(self) -> bool:
        return bool(self.args.kills and self.totals["kills"] >= self.args.kills)

    def timed_out(self, extra: float = 0.0) -> bool:
        return time.monotonic() - self.t0 > self.args.timeout + extra

    def fight(self, serial):
        """One round against `serial`: attack it (once, and once more when it first
        comes adjacent), then the attack spell while the mana allows, else melee."""
        st = self.state()
        mob, why = self.live_target(st, serial)
        if mob is None:
            return
        dist = cheb(self.pos(st), (mob["x"], mob["y"]))
        if self.engaged != serial:
            self.resent_adjacent = dist <= 1
            if not self.attack(serial):
                time.sleep(POLL_S)
            return
        self.engaged_mob = mob
        if not self.resent_adjacent and dist <= 1:
            self.resent_adjacent = True
            self.human.wait("read")
            self.attack(serial)
            return
        mana = st["world"]["self"].get("mana") or 0
        if mana >= self.args.mana_reserve + combat.spell_mana(self.spell) and dist <= SPELL_RANGE \
                and self.spell_block.get(serial, 0) < time.monotonic():
            done = self.cast_at(serial)
            if done:
                self.human.wait("read")
            if done or done is None:
                return
        time.sleep(POLL_S)

    def go_back(self, reason):
        """After leaving: rest and go back in, or stop (a hostile player, --rest-to 0,
        or the rest takes too long)."""
        why, severity = reason
        if severity == "urgent" or self.args.rest_to <= 0 or not self.rest():
            raise Abort(f"left the hunt ({why}); not going back in")
        self.enter()
        self.start_visit(self.state())
        self.to_spot()

    def to_spot(self):
        self.doing("to_spot", f"Heading to the hunting spot {self.spot[0]},{self.spot[1]}", self.spot)
        self.mover.walk_to(lambda: self.spot, 0, "to the spot")

    def hunt(self):
        """Fight at the spot until --kills or --timeout (then finish the fights on us,
        loot and leave), a death or a stop."""
        self.start_visit(self.state())
        self.to_spot()
        while True:
            st = self.state()
            if self.visit is None:                      # left to survive during a speech hold
                self.go_back(self.left_why)
                continue
            attackers = self.attackers(st)
            winding = self.finished() or self.timed_out()
            reason = self.leave_reason(st)
            if reason is None and winding and ((not attackers and not self.corpses) or self.timed_out(60.0)):
                reason = ("done" if self.finished() else "time is up", "info")
            if reason is not None:
                self.leave(st, *reason)
                if reason[1] == "info":
                    return
                self.go_back(reason)
                continue
            if self.pending_speech and not attackers and self.engaged is None:
                self.speech_hold(st)
                continue
            me = st["world"]["self"]
            if me.get("hits") is not None and self.frac(me) < self.args.heal_at and self.heal(st):
                self.human.wait("read")
                continue
            if self.corpses and not attackers:
                self.loot(self.corpses[0])
                continue
            target = self.pick_target(st, pull=not winding)
            if target is not None:
                if target != self.engaged and self.engaged is not None:
                    log(f"switching target to 0x{target:08X}")
                    self.disengage()
                self.fight(target)
                continue
            if self.engaged is not None:
                self.disengage()
            if self.war_mode(st, False):
                log("nothing near: war mode off")
                self.human.wait("read")
            if cheb(self.pos(st), self.spot) > 0:
                self.mover.walk_to(lambda: self.spot, 0, "back to the spot")
            self.doing("wait", f"Waiting for {self.args.target_name} at {self.spot[0]},{self.spot[1]} "
                               f"({self.totals['kills']} killed)", self.spot)
            time.sleep(POLL_S)

    def run(self):
        st = self.link.wait(lambda s: s["movement"]["pos"] is not None
                            and s["movement"]["self_serial"] is not None, 5.0)
        if st is None:
            raise Abort("proxy has no player position yet (log in first)")
        st = self.state()
        if self.args.enter and not self.at_hunt(st):
            self.enter()
            st = self.state()
        if not self.at_hunt(st):
            raise Abort(f"not near the hunting spot {self.spot} (at {self.pos(st)[:2]}); use --enter")
        self.hunt()
        t = self.totals
        log(f"hunt complete: {t['kills']} kill(s), {t['gold']} gold, ~{t['xp']} xp, {t['hits_lost']} hits lost, "
            f"{t['leaves']} leave(s); outside")
        self.doing("done", f"Finished: {t['kills']} kill(s), {t['gold']} gold, ~{t['xp']} xp")


def stop_intent(loop, text):
    """Last words for the visualizer; the proxy may already be gone."""
    try:
        loop.doing("stopped", text[:200])
    except (OSError, ValueError, Abort):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spot", type=int, nargs=2, default=[5535, 529], metavar=("X", "Y"),
                    help="the tile to fight on; leaving starts here (default: the NPD exit tile)")
    ap.add_argument("--exit-dir", type=int, default=4, choices=range(8),
                    help="the step from the spot onto the exit teleporter (default 4 = south)")
    ap.add_argument("--enter", action="store_true",
                    help="start outside: walk to --entry and step --entry-dir to teleport in")
    ap.add_argument("--entry", type=int, nargs=3, default=[1912, 2557, -20], metavar=("X", "Y", "Z"),
                    help="the tile before the entrance teleporter (default: the NPD entrance)")
    ap.add_argument("--entry-dir", type=int, default=0, choices=range(8))
    ap.add_argument("--heal-at", type=float, default=0.75,
                    help="heal below this share of hits: a heal potion if one can be drunk, else a spell")
    ap.add_argument("--gheal-min-missing", type=int, default=None,
                    help="missing hits from which the spell is Greater Heal, not Heal (default: the mana "
                         "break-even for your Magery, healing.gheal_break_even: 19 at Magery 60)")
    ap.add_argument("--leave-at", type=float, default=0.60, help="leave below this share of hits")
    ap.add_argument("--leave-multi-at", type=float, default=0.80,
                    help="leave below this share of hits when two or more mobs are attacking")
    ap.add_argument("--mana-reserve", type=int, default=22,
                    help="mana kept for heals: the attack spell only above this plus its cost")
    ap.add_argument("--spell", default="lightning", help="attack spell (Magery name or 1-64)")
    ap.add_argument("--target-name", default="mongbat", help="words the monster's name must contain")
    ap.add_argument("--pull-range", type=int, default=3, help="engage mobs within this many tiles of the spot")
    ap.add_argument("--kills", type=int, default=0, help="stop after this many kills (0 = until --timeout)")
    ap.add_argument("--timeout", type=float, default=3600.0, help="then finish the fight and leave")
    ap.add_argument("--rest-to", type=float, default=0.95,
                    help="after leaving, rest to this share of hits and go back in (0 = stop after leaving)")
    ap.add_argument("--rest-timeout", type=float, default=900.0)
    ap.add_argument("--loot", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--loot-max", type=int, default=25, help="items taken per corpse")
    ap.add_argument("--human", choices=sorted(PROFILES), default="normal",
                    help="human-texture profile (humanize.py); 'off' for deterministic tests")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--human-fast", type=float, default=1.0,
                    help="scale human delays (offline tests of the normal profile only)")
    ap.add_argument("--no-map", action="store_true",
                    help="plan on walk memory only (offline tests against simulated worlds)")
    ap.add_argument("--max-blocked", type=int, default=20)
    ap.add_argument("--quiet", action="store_true", help="no handoff sound (tests)")
    ap.add_argument("--triage-url", default=triage.DEFAULT_URL,
                    help="laya-serve for speech triage (triage.py); empty = off")
    ap.add_argument("--control-port", type=int, default=25941)
    ap.add_argument("--state-port", type=int, default=25942)
    ap.add_argument("--memory", default=DEFAULT_DB,
                    help="harness memory (SQLite, docs/MEMORY.md): walk memory, junctures, episodes")
    args = ap.parse_args()

    memory = Memory(args.memory)
    link = Link(args.control_port, args.state_port)
    loop = HuntLoop(link, memory, args)
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
        loop.end_visit("stopped")
        memory.close()
    sys.exit(code)


if __name__ == "__main__":
    main()

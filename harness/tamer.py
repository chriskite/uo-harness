"""Fighting with a pet team for the hunt runner (`loop_hunt.py --pets`; docs/HUNT_LOOP.md "Pets").

Learned from Logan Wolf's Mausoleum trip (capture 20261012_210013, 21:16-21:31: 25 kills, Society
job "Kill 25,000 Gold Worth" 290 -> 24,243 in 14 min; docs/NOTES.md "Logan Wolf's tamer fighting"):
a tribard tamer (Taming, Lore, Veterinary, Herding 120; Peacemaking, Discordance, Provocation 80;
Tactics 18) with a bonded phoenix (4 control slots) and a bonded gravebug (1 slot, the tank). He never
turned war mode on or swung: the pets do the killing, he keeps them alive and makes the fights easier.

- **Pets guard, the character never starts the fight** (user, 2026-10-12): a mob aggroes on what
  comes into its aggro range and line of sight; pets on "All Guard Me" engage it before it engages
  us. An aggressive act of ours (Discordance, "All Kill") makes the mob aggro on the character. So
  a target the pets haven't engaged is only approached (to APPROACH_R) and waited for; the bard
  skills go only at a target the pets are already fighting (`engaged`), and "All Kill" only at a mob
  already attacking us that the pets leave alone for KILL_AFTER_S (its aggro is on us anyway).
- **"All Guard Me"** at every arrival (guarding stops at a recall or dungeon level transition:
  wiki Animal Taming, Pet Commands) and after every kill (26 times for 25 kills on the trip; pets
  told "All Kill" go back to the owner after the kill, knowledge #2073). "All Follow Me" before
  every recall or walk out (user directive 2026-10-11, knowledge #17043: a pet was left behind),
  waiting up to FOLLOW_WAIT_S for the pets to come within FOLLOW_R.
- **Pet commands are Razor-shaped speech** (actions.razor_say: hue 0x03B1, double NUL; every pet
  command in Logan Wolf's captures) and only the phrases in COMMANDS. Nothing here or in `ctl` ever
  says or picks anything that releases or transfers a pet (`irreversible`; user 2026-10-12: "make
  sure we don't release my pets on accident").
- **Bard skills** (0x12 type 0x24 "<id> 0", then the target cursor, Razor-fast live: median 0.23 s
  discord, 0.44 s peace): Discordance first (trip: 18 of 30 succeeded, at 1-9 tiles; "(1m)" or
  "(1m 15s)"), then Provocation when another mob is fighting near (2 of 4: the other mob onto a
  second other, or onto our target), then Peacemaking on the target (8 of 14; "(38s)": it stops
  attacking the pets while they kill it). Cooldowns (wiki Peacemaking): a bard skill every
  SKILL_GAP_S (alternating peace/provoke and discord), peace/provoke CALM_SUCCESS_S after a
  success; the server's "You must wait a few moments to use another skill." (cliloc 500118) moves
  the clock. A pacified or provoked creature may suffer a Barding Break (wiki Musicianship): immune
  to peace and provocation for a while ("That target is currently under a barding break effect.").
  "beyond your skill" stops that skill on that creature type for the run.
- **Veterinary supplies** (graphic 0x6496, a stack in a bag; one double-click, no target): heals
  every friendly follower within VET_RANGE and the player at 25 % (patch 2023-08-14), a channel of
  5.0-5.3 s on the trip (43 uses) ended by "You finish using veterinary supplies."; the range check
  is at the finish ("You are too far away from any of your followers to heal them." with the pets
  3-4 tiles off then), heals landed at 0-2 tiles. Bandaging range is 2 only after 5 s stationary,
  1 otherwise (wiki Veterinary): no step during a channel. The Healer's Codex "Without Borders"
  perk adds 1/2/3 tiles (knowledge #11343): `--vet-range`. Used whenever a pet in range is below
  --pet-heal-at or poisoned ("*looks violently ill*" over it), or we are hurt with no heal potion
  ready; a hurt pet out of range is walked to (1 tile) when nothing is on us.
- **Self care**: heal potions as the hunt runner drinks them (Logan's are in a potion satchel in a
  bag: healing.heal_potions searches every bag), no heal spell without Magery. A retreat when a mob
  next to us has us below --retreat-at: run until RETREAT_R tiles from every attacker and let the
  pets hold it (live 21:27:50 a bleed took Logan 72 -> 35 hits; he ran 18 tiles while drinking).
- **Leaving** (besides the hunt runner's rules): a pet dead (a bonded ghost: world `dead`), a pet
  below --pet-leave-at, or a pet out of view for PET_MISSING_S. Not going back in with a pet down.
"""
from __future__ import annotations

import json
import re
import time

import actions
import combat
import healing
import nav
from agent_link import Abort, cheb, log, serial_of

SKILL_PEACE, SKILL_DISCORD, SKILL_PROVO = 9, 15, 22          # skills.mul ids (the 0x12 type 0x24 text)
SKILL_NAMES = {SKILL_PEACE: "Peacemaking", SKILL_DISCORD: "Discordance", SKILL_PROVO: "Provocation"}
VET_SUPPLIES_GRAPHIC = 0x6496
VET_RANGE = 2                 # tiles (wiki Veterinary Supplies, measured on the trip); --vet-range
VET_BUSY_S = 6.5              # a channel the finish line never closed is over after this (measured 5.0-5.3 s)
VET_IDLE_S = 3.0              # "do not require healing": don't press again for this long
USE_RETRY_S = 0.6             # cliloc 500119 "You must wait to perform another action."
TEXT_VET_BEGIN = "You begin using veterinary supplies."
TEXT_VET_FINISH = "You finish using veterinary supplies."
TEXT_VET_NEEDLESS = "You or your nearby followers do not require healing."
TEXT_VET_FAR = "You are too far away from any of your followers to heal them."
CLILOC_ACTION_WAIT = 500119
CLILOC_SKILL_WAIT = 500118    # "You must wait a few moments to use another skill."
NO_LOS_CLILOCS = (500446, 500237)   # "That is too far away." / "Target cannot be seen."
BARD_RANGE = 10               # [INFERENCE] successes seen at 1-9 tiles
SKILL_GAP_S = 5.0             # wiki Peacemaking; live: the next cursor came 5 s after the last use
CALM_SUCCESS_S = 10.0         # peace/provoke after a success (wiki Peacemaking)
SKILL_RETRY_S = 1.0
BREAK_IMMUNE_S = 20.0         # [INFERENCE] a Barding Break lasts CreatureDifficulty / 10 s (patch 2020-04-07)
LOS_BLOCK_S = 10.0
CURSOR_WAIT_S = 2.0           # live: the cursor follows a skill use or "All Kill" within 0.05-0.07 s
ANSWER_WAIT_S = 1.5
ENGAGED_S = 10.0              # a target hurt this recently, a pet within PET_REACH: the pets are on it
PET_REACH = 12                # the phoenix's Epic Barrage reaches 12 tiles (patch 2022-03-16); the gravebug melees
PET_NEAR = 1                  # a war-mode monster this close to a pet that took damage is fighting it
KILL_AFTER_S = 4.0            # a mob attacking us that the pets haven't hurt for this long: "All Kill"
KILL_REPEAT_S = 12.0
PIN_S = 15.0                  # loop_hunt's rule: a target neither fighting the pets nor us this long is dropped...
SKIP_S = 60.0                 # ...and passed over this long
GUARD_REPEAT_S = 8.0
APPROACH_R = 6                # [INFERENCE] aggro range: a target the pets haven't engaged is approached to this
APPROACH_MOVES = 10
WALK_TO_PET_S = 5.0
FOLLOW_R = 3                  # mount.PET_RANGE: a following pet stays this close
# "All Follow Me" before a recall: how long to wait for the pets to come within FOLLOW_R, by the leave's
# severity. A hostile player: not at all. Our own hits low: no standing wait (the simulator, docs/SIM.md:
# a 3 s wait at ~45 % hits killed us in 8 of 16 runs, a bleed finishing us during the cast) but a fall back
# (FALLBACK_*): run from the attackers while the pets come, as Logan Wolf did at 35 % (21:27:52), since a
# recall without waiting left a pet behind in 12 of 16. Leaving for a pet while we are fine (follow()
# upgrades "attention" to "info" then) and planned leaves: 10 s.
FOLLOW_WAIT_S = {"urgent": 0.0, "attention": 0.0, "info": 10.0}
FALLBACK_R = 4                # run until this far from every attacker...
FALLBACK_MOVES = 10           # ...at most this many steps...
FALLBACK_S = 4.0              # ...or this long, stopping as soon as every pet is within FOLLOW_R
PET_MISSING_S = 30.0
RETREAT_R = 7
RETREAT_REPEAT_S = 15.0
POISON_S = 10.0               # "*looks violently ill*" over a pet: poisoned for this long (vet supplies cure)
OWN_PETS_KEY = "own_pets"     # memory-store meta: {character: [{serial, name}], ...}
FOUGHT_S = 60.0              # a mob the pets fought that dies within this is our kill (its corpse looted)
# Monsters with an area Charge (knowledge #14625: it teleports up to 12 tiles next to its target and hits
# up to 3 within 1 tile; live 21:21:14 a nightstalker's Charge hit the gravebug -37 and Logan -42 at once).
# More are learnt from a "*charges*" over a creature.
CHARGERS = {"nightstalker"}
TEXT_CHARGES = "*charges*"
CHARGE_SAFE_R = 3             # tiles from a charger and from a pet beside it: clear of where its Charge lands
STANDOFF_MOVES = 6
STANDOFF_REPEAT_S = 2.0

# The only phrases ever said to the pets, as Logan Wolf's hotkeys say them
COMMANDS = {c.lower(): c for c in ("All Guard Me", "All Follow Me", "All Kill", "All Stop", "All Stay", "All Come")}
# Anything that gives a pet away: "<name> release", "all release", the context menu's Release/Transfer
IRREVERSIBLE = re.compile(r"\b(release|transfer)\b", re.I)
ANSWER_OK = re.compile(r"^You play successfully, (disrupting|pacifying|provoking)")
ANSWER_FAIL = re.compile(r"^You fail to (discord|pacify|incite)")
DURATION = re.compile(r"\((?:(\d+)m)?\s*(?:(\d+)s)?\)\s*$")
TEXT_CALM_WAIT = "You must wait a few moments before you may provoke or pacify another creature."
TEXT_IMMUNE = "That target is currently under a barding break effect."
TEXT_BEYOND = "beyond your skill"
TEXT_INCITE_SECOND = "Whom do you wish them to attack?"
TEXT_POISONED = "*looks violently ill*"
OK_SKILL = {"disrupting": SKILL_DISCORD, "pacifying": SKILL_PEACE, "provoking": SKILL_PROVO}
FAIL_SKILL = {"discord": SKILL_DISCORD, "pacify": SKILL_PEACE, "incite": SKILL_PROVO}
EFFECT = {SKILL_DISCORD: "discord", SKILL_PEACE: "calm", SKILL_PROVO: "calm"}
DEFAULT_S = {SKILL_DISCORD: 60.0, SKILL_PEACE: 30.0, SKILL_PROVO: 30.0}   # when the answer names no duration


def irreversible(text: str) -> str | None:
    """Why `text` (speech, a context menu entry) could give a pet away, else None."""
    m = IRREVERSIBLE.search(text or "")
    return f"{m.group(0)!r} could release or transfer a pet" if m else None


def command(text: str) -> bytes:
    """The 0xAD for a pet command in COMMANDS (any case), Razor-shaped; ValueError otherwise."""
    why = irreversible(text)
    if why:
        raise ValueError(why)
    phrase = COMMANDS.get(" ".join((text or "").split()).lower())
    if phrase is None:
        raise ValueError(f"{text!r} is not a pet command we say ({sorted(COMMANDS.values())})")
    return actions.razor_say(phrase)


def duration(text: str) -> float | None:
    """Seconds in a bard success line's "(1m 15s)" / "(38s)" / "(1m)", else None."""
    m = DURATION.search(text or "")
    if not m or not (m.group(1) or m.group(2)):
        return None
    return 60.0 * int(m.group(1) or 0) + int(m.group(2) or 0)


def bard_answer(e: dict):
    """(skill id or None, outcome, seconds) for a server answer to a bard skill, else None. Outcomes:
    "ok" (seconds: its duration), "fail", "beyond" (a fail beyond our skill), "calm_wait",
    "skill_wait", "immune", "no_los"."""
    if e.get("ev") == "cliloc":
        c = e.get("cliloc")
        if c == CLILOC_SKILL_WAIT:
            return None, "skill_wait", None
        if c in NO_LOS_CLILOCS:
            return None, "no_los", None
        return None
    if e.get("ev") != "speech_heard":
        return None
    text = e.get("text") or ""
    if m := ANSWER_OK.match(text):
        sid = OK_SKILL[m.group(1)]
        return sid, "ok", duration(text) or DEFAULT_S[sid]
    if m := ANSWER_FAIL.match(text):
        return FAIL_SKILL[m.group(1)], "beyond" if TEXT_BEYOND in text else "fail", None
    if text == TEXT_CALM_WAIT:
        return None, "calm_wait", None
    if text == TEXT_IMMUNE:
        return None, "immune", None
    return None


def type_key(name: str | None) -> str:
    return re.sub(r"^(a|an|the)\s+", "", (name or "").strip().lower())


# ------------------------------------------------------------ the roster
def remembered(mem, character: str | None) -> list[int]:
    import task_wrap
    rows = json.loads(task_wrap.meta_get(mem, OWN_PETS_KEY) or "{}").get((character or "").strip().lower()) or []
    return [serial_of(r["serial"]) for r in rows]


def remember(mem, character: str | None, roster: dict):
    import task_wrap
    if not character:
        return
    pets = json.loads(task_wrap.meta_get(mem, OWN_PETS_KEY) or "{}")
    pets[character.strip().lower()] = [{"serial": f"0x{s:08X}", "name": n} for s, n in roster.items()]
    task_wrap.meta_set(mem, OWN_PETS_KEY, json.dumps(pets))


def resolve(world: dict, pos, wanted, known) -> tuple[dict, str]:
    """({serial: name}, how) of our pets: `wanted` (--pet: serials or names, any case) among the
    mobiles in view; else the `known` serials (remembered) in view; else the tamed pets (a
    "(bonded)" / "(tame)" line) within FOLLOW_R of us. Never a human body."""
    import mount
    mobs = world.get("mobiles") or {}
    me = serial_of((world.get("self") or {}).get("serial") or 0)

    def name(s):
        m = mobs.get(f"0x{s:08X}") or {}
        return m.get("name") or (world.get("labels") or {}).get(f"0x{s:08X}") or f"0x{s:08X}"
    if wanted:
        out, missing = {}, []
        for w in wanted:
            try:
                s = int(w, 0)
            except ValueError:
                # not a hostile namesake: the vampire countess makes attackable copies of our pets with
                # their names (capture 20261012_210013, docs/NOTES.md); ours are notoriety 2
                s = next((serial_of(k) for k, m in mobs.items() if (m.get("name") or "").lower() == w.lower()
                          and serial_of(k) != me and m.get("graphic") not in mount.HUMAN_BODIES
                          and m.get("notoriety") not in combat.ATTACKABLE_NOTORIETY), None)
            if s is None or f"0x{s:08X}" not in mobs:
                missing.append(w)
            else:
                out[s] = name(s)
        if missing:
            raise Abort(f"--pet {', '.join(missing)}: not in view")
        return out, "--pet"
    seen = {s: name(s) for s in known if f"0x{s:08X}" in mobs}
    if seen:
        return seen, "remembered"
    near = mount.pets_near({"movement": {"pos": pos, "self_serial": me}, "world": world}, FOLLOW_R)
    return {s: name(s) for _, s, _ in near}, f"tamed pets within {FOLLOW_R} tiles"


class Team:
    """The pet team's side of a hunt (module docstring). The host `h` is the HuntLoop: its link,
    human, mover, state reads, target guard and engagement records."""

    def __init__(self, h):
        self.h, self.a = h, h.args
        self.roster = {}                 # serial -> name
        self.ev_upto = 0
        self.guard_needed = True
        self.guard_t = -1e9
        self.vet_busy_until = 0.0        # a channel is running (or a refusal asks us to wait) until then
        self.vet_t = 0.0                 # the last channel's start
        self.no_vet_posted = False
        self.skill_ready = 0.0           # any bard skill
        self.calm_ready = 0.0            # peace / provoke
        self.effects = {}                # serial -> {"discord": until, "calm": until, "immune": until, "los": until}
        self.beyond = set()              # (skill, creature type) failed "beyond your skill"
        self.kill_t = {}                 # serial -> our last "All Kill" at it
        self.attack_t = {}               # serial -> first seen attacking us (this engagement)
        self.poisoned_t = {}             # pet serial -> "*looks violently ill*" over it
        self.seen_t = {}                 # pet serial -> last seen in view
        self.walk_pet_t = 0.0
        self.retreat_t = -1e9
        self.counts = {}
        self.fought = {}                 # serial -> (last-seen mob dict, monotonic): mobs the pets fought
        self.chargers = set(CHARGERS)    # creature types with an area Charge (known + seen this run)
        self.standoff_t = -1e9

    # ------------------------------------------------------------ bookkeeping
    def count(self, what: str, n: int = 1):
        self.counts[what] = self.counts.get(what, 0) + n
        self.h.count(what, n)

    def summary(self) -> dict:
        return {"pets": {f"0x{s:08X}": n for s, n in self.roster.items()}, **self.counts}

    def setup(self, st):
        """The roster (resolve), logged and remembered for this character; Abort without a pet."""
        world = st["world"]
        character = world["self"].get("name")
        self.roster, how = resolve(world, self.h.pos(st), self.a.pet or [], remembered(self.h.memory, character))
        if not self.roster:
            raise Abort("--pets: no pet of ours in view (claim them first, or name them with --pet)")
        log(f"pets ({how}): " + ", ".join(f"{n} 0x{s:08X}" for s, n in self.roster.items()))
        remember(self.h.memory, character, self.roster)
        now = time.monotonic()
        self.seen_t = {s: now for s in self.roster}
        self.ev_upto = len(self.h.link.events)

    def pets(self, st) -> list[dict]:
        """Our pets in view: serial, name, frac (hits share; pets' hits come as a share of 100),
        dist, dead, x, y, poisoned."""
        world, pos, now = st["world"], self.h.pos(st), time.monotonic()
        out = []
        for s, name in self.roster.items():
            m = world["mobiles"].get(f"0x{s:08X}")
            if m is None or m.get("x") is None:
                continue
            self.seen_t[s] = now
            out.append({"serial": s, "name": name, "frac": self.h.frac(m), "dist": cheb(pos, (m["x"], m["y"])),
                        "dead": bool(m.get("dead")), "x": m["x"], "y": m["y"],
                        "poisoned": bool(m.get("poisoned")) or now - self.poisoned_t.get(s, -1e9) < POISON_S})
        return out

    def update(self, st):
        """Events since the last look: the vet channel's end, Barding Breaks over creatures, pets
        poisoned, dead creatures forgotten."""
        now = time.monotonic()
        evs = self.h.link.events
        for e in evs[self.ev_upto:]:
            kind, text = e.get("ev"), e.get("text") or ""
            s = serial_of(e["serial"]) if e.get("serial") is not None else None
            if kind == "speech_heard" and s in (None, 0, 0xFFFFFFFF):
                if text in (TEXT_VET_FINISH, TEXT_VET_FAR):
                    self.vet_busy_until = min(self.vet_busy_until, now)
                    if text == TEXT_VET_FAR:
                        log("vet supplies: too far from every pet at the finish")
            elif kind == "speech_heard" and s is not None:
                if s in self.roster and text == TEXT_POISONED:
                    self.poisoned_t[s] = now
                elif text == TEXT_CHARGES and s not in self.roster:
                    m = st["world"]["mobiles"].get(f"0x{s:08X}") or {}
                    kind = type_key((st["world"].get("labels") or {}).get(f"0x{s:08X}") or m.get("name"))
                    if kind and kind not in self.chargers:
                        self.chargers.add(kind)
                        log(f"{kind} charges: standing {CHARGE_SAFE_R} tiles off it and the pet it fights")
                elif "barding break" in text.lower() and s not in self.roster:
                    fx = self.effects.setdefault(s, {})
                    fx["calm"], fx["immune"] = 0.0, now + BREAK_IMMUNE_S
                    log(f"barding break on 0x{s:08X}: no peace or provocation at it for {BREAK_IMMUNE_S:.0f} s")
            elif kind == "mobile_death" and s is not None:
                self.effects.pop(s, None)
                self.attack_t.pop(s, None)
        self.ev_upto = len(evs)

    # ------------------------------------------------------------ what the pets are doing
    def engaged(self, st, serial: int) -> bool:
        """The pets are fighting `serial`: it was hurt within ENGAGED_S with a live pet within PET_REACH."""
        if time.monotonic() - self.h.hurt_t.get(serial, -1e9) > ENGAGED_S:
            return False
        m = st["world"]["mobiles"].get(f"0x{serial:08X}")
        return m is not None and m.get("x") is not None and any(
            not p["dead"] and cheb((p["x"], p["y"]), (m["x"], m["y"])) <= PET_REACH for p in self.pets(st))

    def pet_attackers(self, st) -> set[int]:
        """Monsters fighting our pets: in war mode within PET_NEAR of a pet that took damage lately."""
        import threats
        world, now = st["world"], time.monotonic()
        labels = world.get("labels") or {}
        hurt = [p for p in self.pets(st) if not p["dead"] and now - self.h.hurt_t.get(p["serial"], -1e9) <= ENGAGED_S]
        out = set()
        for key, mob in world["mobiles"].items():
            s = serial_of(key)
            if s in self.roster or s in self.h.dead or mob.get("x") is None or mob.get("pet") \
                    or mob.get("notoriety") not in combat.ATTACKABLE_NOTORIETY \
                    or not (mob.get("flags") or 0) & threats.FLAG_WARMODE:
                continue
            if any(cheb((p["x"], p["y"]), (mob["x"], mob["y"])) <= PET_NEAR for p in hurt) \
                    and threats.identify(mob, labels.get(key))[0] == "monster":
                out.add(s)
                self.fought[s] = (mob, now)
        return out

    def others(self, st, serial: int) -> list[int]:
        """Other monsters in the fight within BARD_RANGE, nearest first: attacking us or the pets."""
        pos = self.h.pos(st)
        fighting = {serial_of(a["serial"]) for a in self.h.attackers(st)} | self.pet_attackers(st)
        out = []
        for s in fighting - {serial}:
            mob, _ = self.h.live_target(st, s, BARD_RANGE)
            if mob is not None:
                out.append((cheb(pos, (mob["x"], mob["y"])), s))
        return [s for _, s in sorted(out)]

    # ------------------------------------------------------------ rules
    def leave_reason(self, st):
        """(why, severity) to leave for the pets' sake, else None."""
        now = time.monotonic()
        seen = {p["serial"]: p for p in self.pets(st)}
        for s, name in self.roster.items():
            p = seen.get(s)
            if p is None:
                if now - self.seen_t.get(s, now) > PET_MISSING_S:
                    return f"{name} out of view for {PET_MISSING_S:.0f} s", "attention"
                continue
            if p["dead"]:
                return f"{name} is dead", "attention"
            if p["frac"] < self.a.pet_leave_at:
                return f"{name} at {p['frac']:.0%} hits (below {self.a.pet_leave_at:.0%})", "attention"
        return None

    def down(self, st) -> str | None:
        """Why not to go back in: a pet dead or out of view."""
        seen = {p["serial"]: p for p in self.pets(st)}
        for s, name in self.roster.items():
            if s not in seen:
                return f"{name} is not with us"
            if seen[s]["dead"]:
                return f"{name} is dead"
        return None

    # ------------------------------------------------------------ speech
    def say(self, text: str) -> bool:
        self.h.human.wait("speak")
        self.h.link.act(command(text))
        self.count("commands")
        log(f'said "{COMMANDS[text.lower()]}"')
        return True

    def guard(self) -> bool:
        if time.monotonic() - self.guard_t < GUARD_REPEAT_S:
            return False
        self.h.doing("pets", "Telling the pets to guard me")
        self.say("all guard me")
        self.guard_t, self.guard_needed = time.monotonic(), False
        return True

    def arrived(self):
        """After a recall, a teleporter or a dungeon level: guarding stopped (wiki)."""
        self.guard_needed, self.guard_t = True, -1e9
        now = time.monotonic()
        self.seen_t = {s: now for s in self.roster}

    def on_kill(self, serial: int):
        self.guard_needed = True
        self.effects.pop(serial, None)
        self.attack_t.pop(serial, None)
        self.fought.pop(serial, None)

    def fought_mob(self, serial: int) -> dict | None:
        """The last-seen dict of a mob the pets fought within FOUGHT_S (popped), else None."""
        got = self.fought.pop(serial, None)
        if got is None or time.monotonic() - got[1] > FOUGHT_S:
            return None
        return got[0]

    def rested(self, st) -> bool:
        """Resting: every pet with us at --pet-heal-at, by the vet supplies or, with none left, by
        natural regeneration (--rest-timeout bounds the rest). Going back in with a hurt pet made the
        simulator's runs recall in and out hundreds of times an hour (docs/SIM.md)."""
        return all(p["frac"] >= self.a.pet_heal_at for p in self.pets(st) if not p["dead"])

    def follow(self, severity: str = "info"):
        """'All Follow Me' before a recall or a walk out, then up to FOLLOW_WAIT_S[severity] for every pet
        (a ghost too: it comes along) to come within FOLLOW_R (user directive 2026-10-11); hurt
        ("attention" below --heal-at): fall back from the attackers meanwhile instead of standing."""
        self.say("all follow me")
        st = self.h.link.state()
        me = st["world"]["self"]
        hurt = me.get("hits") is not None and self.h.frac(me) < self.a.heal_at
        if severity == "attention" and not hurt:
            severity = "info"                # leaving for a pet's sake, we aren't hurt: wait for it in full
        if severity == "attention":
            self.fall_back(st)
        end = time.monotonic() + FOLLOW_WAIT_S.get(severity, FOLLOW_WAIT_S["info"])
        while time.monotonic() < end and not self.together(self.h.link.state()):
            time.sleep(0.4)
        far = [f"{p['name']} at {p['dist']}" for p in self.pets(self.h.link.state()) if p["dist"] > FOLLOW_R]
        if far:
            log("pets not all within reach after 'All Follow Me': " + ", ".join(far))

    def together(self, st) -> bool:
        """Every pet of the roster in view within FOLLOW_R."""
        near = {p["serial"] for p in self.pets(st) if p["dist"] <= FOLLOW_R}
        return all(s in near for s in self.roster)

    def fall_back(self, st):
        """Run from the attackers (FALLBACK_R) while the following pets come, stopping once they're
        together, after FALLBACK_MOVES steps or FALLBACK_S."""
        h = self.h
        if self.together(st):
            return
        tiles = [(m["x"], m["y"]) for a in h.attackers(st) if (m := st["world"]["mobiles"].get(a["serial"]))
                 and m.get("x") is not None]
        end, label = time.monotonic() + FALLBACK_S, "falling back"
        if not tiles:
            while time.monotonic() < end and not self.together(h.link.state()):
                time.sleep(0.3)
            return
        h.doing("leave", "Falling back while the pets come")
        try:
            h.mover.walk_to(None, 0, label, goal_fn=nav.beyond(tiles, FALLBACK_R), urgent=True,
                            max_moves=FALLBACK_MOVES,
                            stop=lambda s: self.together(s) or time.monotonic() > end)
        except Abort as e:
            if not str(e).startswith(label):
                raise
            log(f"fall back: {e}")

    def landed(self, st, leg: str):
        """After a recall: every pet came along, else a `pet_left_behind` juncture."""
        missing = [n for s, n in self.roster.items() if s not in {p["serial"] for p in self.pets(st)}]
        self.arrived()
        if missing:
            data = {"leg": leg, "missing": missing, "pets": self.summary()["pets"]}
            self.h.memory.juncture("hunt", "pet_left_behind",
                                   f"Pets not with us after the recall ({leg}): {', '.join(missing)}", "urgent", data)
            log(f"PETS LEFT BEHIND ({leg}): {', '.join(missing)}")

    # ------------------------------------------------------------ care: retreat, vet, guard
    def care(self, st) -> bool:
        """One care action, True when something was done: retreat, vet supplies, guard, walk to a hurt pet."""
        self.update(st)
        return self.retreat(st) or self.stand_off(st) or self.vet(st) or (self.guard_needed and self.guard()) \
            or self.to_pet(st)

    def charge_zone(self, st) -> set:
        """Where a Charge would land its hit: every charger in war mode in view and each live pet beside
        one (the one it fights)."""
        import threats
        world = st["world"]
        labels = world.get("labels") or {}
        pets = [p for p in self.pets(st) if not p["dead"]]
        zone = set()
        for key, mob in world["mobiles"].items():
            s = serial_of(key)
            if s in self.roster or s in self.h.dead or mob.get("x") is None \
                    or not (mob.get("flags") or 0) & threats.FLAG_WARMODE \
                    or type_key(labels.get(key) or mob.get("name")) not in self.chargers:
                continue
            c = (mob["x"], mob["y"])
            zone.add(c)
            zone.update((p["x"], p["y"]) for p in pets if cheb((p["x"], p["y"]), c) <= PET_NEAR)
        return zone

    def heal_at(self, p: dict, zone: set) -> float:
        """The hits share below which pet `p` gets the vet supplies: --pet-heal-at, but only
        --standoff-vet-at for a pet a charger fights while our supplies don't reach CHARGE_SAFE_R
        (stepping in to heal it puts us in the Charge's reach: user 2026-10-13). --vet-range 3+ (the
        Healer's Codex 'Without Borders') heals it from outside."""
        if self.a.vet_range < CHARGE_SAFE_R and (p["x"], p["y"]) in zone:
            return min(self.a.pet_heal_at, self.a.standoff_vet_at)
        return self.a.pet_heal_at

    def stand_off(self, st) -> bool:
        """A charger in the fight: keep CHARGE_SAFE_R from it and the pet beside it (out of its Charge),
        unless that pet needs healing (heal_at) and our supplies don't reach that far; never mid-channel."""
        h, now = self.h, time.monotonic()
        if now < self.vet_busy_until or now - self.standoff_t < STANDOFF_REPEAT_S:
            return False
        zone = self.charge_zone(st)
        pos = h.pos(st)
        if not zone or all(cheb(pos, t) >= CHARGE_SAFE_R for t in zone):
            return False
        if self.a.vet_range < CHARGE_SAFE_R \
                and any(p["frac"] < self.heal_at(p, zone) or p["poisoned"] for p in self.pets(st)
                        if not p["dead"] and (p["x"], p["y"]) in zone) \
                and healing.in_pack(st["world"], h.me(st), VET_SUPPLIES_GRAPHIC):
            return False                                  # step in (or stay) to heal the tank
        self.standoff_t = now
        label = "out of charge reach"
        h.doing("standoff", f"Standing off a charger ({len(zone)} tile(s) in its reach)")
        self.count("standoffs")
        try:
            h.mover.walk_to(None, 0, label, goal_fn=nav.beyond(sorted(zone), CHARGE_SAFE_R - 1),
                            max_moves=STANDOFF_MOVES)
        except Abort as e:
            if not str(e).startswith(label):
                raise
            log(f"stand-off cut short: {e}")
        return True

    def retreat(self, st) -> bool:
        h, now = self.h, time.monotonic()
        me = st["world"]["self"]
        if me.get("hits") is None or h.frac(me) >= self.a.retreat_at or now - self.retreat_t < RETREAT_REPEAT_S:
            return False
        near = [a for a in h.attackers(st) if a["dist"] <= 1]
        if not near:
            return False
        tiles = []
        for a in near:
            m = st["world"]["mobiles"].get(a["serial"]) or {}
            if m.get("x") is not None:
                tiles.append((m["x"], m["y"]))
        self.retreat_t = now
        label = "retreat"
        h.doing("retreat", f"Backing off from {near[0]['name'] or near[0]['serial']} "
                           f"({me.get('hits')}/{me.get('hits_max')} hits)")
        log(f"retreating {RETREAT_R} tiles from {', '.join(str(a['name']) for a in near)} at "
            f"{me.get('hits')}/{me.get('hits_max')} hits; the pets hold it")
        self.count("retreats")
        try:
            h.mover.walk_to(None, 0, label, goal_fn=nav.beyond(tiles, RETREAT_R - 1), urgent=True,
                            max_moves=2 * RETREAT_R)
        except Abort as e:
            if not str(e).startswith(label):
                raise
            log(f"retreat cut short: {e}")
        return True

    def vet_needed(self, st, pets) -> str | None:
        me = st["world"]["self"]
        zone = self.charge_zone(st)
        hurt = [p for p in pets if not p["dead"] and p["dist"] <= self.a.vet_range
                and (p["frac"] < self.heal_at(p, zone) or p["poisoned"])]
        if hurt:
            p = min(hurt, key=lambda p: p["frac"])
            return f"{p['name']} at {p['frac']:.0%}" + (" (poisoned)" if p["poisoned"] else "")
        if me.get("hits") is not None and self.h.frac(me) < self.a.heal_at \
                and not (healing.heal_potions(st["world"], self.h.me(st)) and self.h.potions.ready(time.monotonic())):
            return f"us at {me.get('hits')}/{me.get('hits_max')} (no heal potion ready)"
        return None

    def vet(self, st) -> bool:
        """Veterinary supplies when vet_needed and no channel runs: the stock double-click on the
        smallest stack (healing.in_pack: any bag depth, no bag opened), its answer read."""
        h, now = self.h, time.monotonic()
        if now < self.vet_busy_until:
            return False
        why = self.vet_needed(st, self.pets(st))
        if why is None:
            return False
        stacks = healing.in_pack(st["world"], h.me(st), VET_SUPPLIES_GRAPHIC)
        if not stacks:
            if not self.no_vet_posted:
                self.no_vet_posted = True
                h.memory.juncture("hunt", "low_supplies", f"No veterinary supplies left ({why})", "attention",
                                  {"item": "veterinary supplies", "why": why})
                log("low supplies: no veterinary supplies")
            return False
        serial, it = stacks[0]
        h.doing("vet", f"Veterinary supplies: {why}")
        h.human.wait("use")
        mark = len(h.link.events)
        h.link.act(actions.dclick(serial))
        got = h.wait_for(lambda s: any(self._vet_reply(e) for e in h.link.events[mark:]), 1.5)
        reply = next((r for e in h.link.events[mark:] if (r := self._vet_reply(e))), None) if got else None
        now = time.monotonic()
        if reply == "begin":
            self.vet_t, self.vet_busy_until = now, now + VET_BUSY_S
            self.count("vet")
            log(f"vet supplies ({it.get('amount')} left): {why}")
        elif reply == "needless":
            self.vet_busy_until = now + VET_IDLE_S
        else:
            self.vet_busy_until = now + USE_RETRY_S
        return True

    @staticmethod
    def _vet_reply(e) -> str | None:
        if e.get("ev") == "speech_heard":
            return {TEXT_VET_BEGIN: "begin", TEXT_VET_NEEDLESS: "needless", TEXT_VET_FAR: "far"}.get(e.get("text"))
        if e.get("ev") == "cliloc" and e.get("cliloc") == CLILOC_ACTION_WAIT:
            return "wait"
        return None

    def to_pet(self, st) -> bool:
        """A pet below --pet-heal-at beyond --vet-range: walk next to it (bandaging range is 1 after a
        step) when nothing is on us, no channel runs and supplies are there."""
        h, now = self.h, time.monotonic()
        if now < self.vet_busy_until or now - self.walk_pet_t < WALK_TO_PET_S or h.attackers(st) \
                or not healing.in_pack(st["world"], h.me(st), VET_SUPPLIES_GRAPHIC):
            return False
        zone = self.charge_zone(st)
        far = [p for p in self.pets(st) if not p["dead"] and p["dist"] > self.a.vet_range
               and p["dist"] <= combat.VIEW_RANGE and (p["frac"] < self.heal_at(p, zone) or p["poisoned"])]
        if not far:
            return False
        p = min(far, key=lambda p: p["frac"])
        self.walk_pet_t = now
        label = "to a hurt pet"
        h.doing("vet", f"Going to {p['name']} ({p['frac']:.0%})", (p["x"], p["y"]))

        def where():
            m = self.h.link.last["world"]["mobiles"].get(f"0x{p['serial']:08X}") or p
            return m["x"], m["y"]
        try:
            h.mover.walk_to(where, 1, label, max_moves=combat.VIEW_RANGE)
        except Abort as e:
            if not str(e).startswith(label):
                raise
            log(f"walk to {p['name']}: {e}")
        return True

    # ------------------------------------------------------------ the fight
    def fight(self, serial: int):
        """One round against `serial` (module docstring): approach and wait while the pets haven't
        engaged it; "All Kill" at a mob on us they leave alone; else the bard skills at it."""
        h, now = self.h, time.monotonic()
        st = h.state()
        mob, why = h.live_target(st, serial)
        if mob is None:
            return
        name = mob.get("name") or f"0x{serial:08X}"
        if h.engaged != serial:
            h.engage(st, serial, mob)
            log(f"target {name} 0x{serial:08X} at {cheb(h.pos(st), (mob['x'], mob['y']))} tiles")
        h.engaged_mob = mob
        on_us = serial in {serial_of(a["serial"]) for a in h.attackers(st)}
        if on_us:
            self.attack_t.setdefault(serial, now)
        else:
            self.attack_t.pop(serial, None)
        hurt = h.hurt_t.get(serial, -1e9)
        h.progress_t = max(h.progress_t, hurt, now if on_us else 0.0)
        if now - h.progress_t > PIN_S:
            log(f"dropping {name} 0x{serial:08X}: neither fighting the pets nor us for {PIN_S:.0f} s; "
                f"skipping it for {SKIP_S:.0f} s")
            h.skip[serial] = now + SKIP_S
            h.disengage("dropped")
            return
        engaged = self.engaged(st, serial)
        if engaged or on_us:
            self.fought[serial] = (mob, now)
        if on_us and not engaged and now - self.attack_t[serial] >= KILL_AFTER_S \
                and now - self.kill_t.get(serial, -1e9) >= KILL_REPEAT_S \
                and self.effects.get(serial, {}).get("los", 0) < now:
            self.all_kill(serial)
            return
        if not engaged:
            if not on_us and cheb(h.pos(st), (mob["x"], mob["y"])) > APPROACH_R:
                self.approach(serial, mob)
                return
            h.doing("wait", f"Pets on guard; waiting for {name} to come on", (mob["x"], mob["y"]), serial)
            time.sleep(0.4)
            return
        if self.bard(st, serial, mob):
            return
        h.doing("fight", f"The pets are fighting {name}", (mob["x"], mob["y"]), serial)
        time.sleep(0.4)

    def approach(self, serial: int, mob: dict):
        """Walk toward a target the pets haven't engaged until APPROACH_R (its aggro range): the
        pets on guard engage it when it comes on. Stops as soon as it is hurt or attacks us."""
        h, label = self.h, "toward a target"
        h.doing("approach", f"Approaching {mob.get('name') or hex(serial)}", (mob["x"], mob["y"]), serial)

        def where():
            m = h.link.last["world"]["mobiles"].get(f"0x{serial:08X}") or mob
            return m["x"], m["y"]

        def stop(st):
            return self.engaged(st, serial) or serial in {serial_of(a["serial"]) for a in h.attackers(st)}
        try:
            h.mover.walk_to(where, APPROACH_R, label, max_moves=APPROACH_MOVES, stop=stop)
        except Abort as e:
            if not str(e).startswith(label):
                raise
            log(f"approach: {e}")

    def bard(self, st, serial: int, mob: dict) -> bool:
        """The next bard skill at a target the pets fight: discord, provoke another, peace. True when
        one went out."""
        now = time.monotonic()
        if now < self.skill_ready:
            return False
        fx = self.effects.setdefault(serial, {})
        if fx.get("los", 0) > now or cheb(self.h.pos(st), (mob["x"], mob["y"])) > BARD_RANGE:
            return False
        kind = type_key(mob.get("name"))
        if self.a.discord and fx.get("discord", 0) <= now and (SKILL_DISCORD, kind) not in self.beyond:
            return self.use(SKILL_DISCORD, serial)
        if now < self.calm_ready:
            return False
        if self.a.provoke:
            others = [s for s in self.others(st, serial)
                      if self.effects.get(s, {}).get("calm", 0) <= now and self.effects.get(s, {}).get("immune", 0) <= now
                      and (SKILL_PROVO, type_key((st["world"]["mobiles"].get(f"0x{s:08X}") or {}).get("name")))
                      not in self.beyond]
            if others:
                return self.use(SKILL_PROVO, others[0], others[1] if len(others) > 1 else serial)
        if self.a.peace and fx.get("calm", 0) <= now and fx.get("immune", 0) <= now \
                and (SKILL_PEACE, kind) not in self.beyond:
            return self.use(SKILL_PEACE, serial)
        return False

    def _cursor(self, mark: int, not_id=None):
        """The target cursor once a `target` event follows `mark` (and isn't `not_id`), else None (a
        skill-wait cliloc or the timeout)."""
        h = self.h
        end = time.monotonic() + CURSOR_WAIT_S
        while time.monotonic() < end:
            st = h.state()
            cur = st["world"].get("target") or {}
            evs = h.link.events[mark:]
            if any(e.get("ev") == "target" for e in evs) and cur.get("active") \
                    and cur.get("cursor_id") is not None and cur.get("cursor_id") != not_id:
                return cur
            if any(e.get("ev") == "cliloc" and e.get("cliloc") in (CLILOC_SKILL_WAIT, CLILOC_ACTION_WAIT) for e in evs) \
                    or any(bard_answer(e) and bard_answer(e)[1] in ("calm_wait", "skill_wait") for e in evs):
                return None
            time.sleep(0.05)
        return None

    def _aim(self, cur, serial: int, rng: int) -> bool:
        """Answer the cursor `cur` with `serial` after a human aim pause and a fresh read: the target
        still live (the monsters-only guard) and no leave rule firing; else the stock Esc."""
        h = self.h
        h.human.wait("aim")
        st = h.state()
        if not h.cursor_still(st, cur):
            log("the cursor went away before the target answer")
            return False
        mob, why = h.live_target(st, serial, rng)
        if mob is None or h.leave_reason(st):
            log(f"not answering the cursor with 0x{serial:08X}: {why or 'a leave rule fired'}; cancelling")
            h.cancel(cur)
            return False
        h.link.act(combat.target_mobile(cur, serial, mob))
        return True

    def use(self, sid: int, serial: int, second: int | None = None) -> bool:
        """A bard skill at `serial` (Provocation: `serial` incited onto `second`); the answer sets the
        clocks and the target's effects. True when the skill went out."""
        h = self.h
        st = h.state()
        mob, _ = h.live_target(st, serial, BARD_RANGE)
        if mob is None:
            return False
        name = mob.get("name") or f"0x{serial:08X}"
        what = SKILL_NAMES[sid] + f" on {name}" + ("" if second is None else f" onto 0x{second:08X}")
        h.doing("bard", what, (mob["x"], mob["y"]), serial)
        h.human.wait("use")
        mark = len(h.link.events)
        h.link.act(actions.use_skill(sid))
        now = time.monotonic()
        self.skill_ready = now + SKILL_GAP_S
        self.count("bard")
        cur = self._cursor(mark)
        if cur is None:
            refused = [bard_answer(e) for e in h.link.events[mark:] if bard_answer(e)]
            if any(r[1] == "calm_wait" for r in refused):
                self.calm_ready = time.monotonic() + SKILL_GAP_S
            else:
                self.skill_ready = time.monotonic() + SKILL_RETRY_S
            log(f"{what}: no target cursor ({[r[1] for r in refused] or 'no answer'})")
            return True
        if not self._aim(cur, serial, BARD_RANGE):
            return True
        if second is not None:
            mark2 = len(h.link.events)
            cur2 = self._cursor(mark2, not_id=None)
            if cur2 is None or not self._aim(cur2, second, BARD_RANGE):
                log(f"{what}: no second target")
                return True
        ans = mark
        h.wait_for(lambda s: any(bard_answer(e) for e in h.link.events[ans:]
                                 if not (e.get("ev") == "speech_heard" and e.get("text") == TEXT_INCITE_SECOND)),
                   ANSWER_WAIT_S)
        self.answered(sid, serial, second, name, [bard_answer(e) for e in h.link.events[ans:] if bard_answer(e)])
        h.human.wait("read")
        return True

    def answered(self, sid: int, serial: int, second, name: str, answers: list):
        now = time.monotonic()
        fx = self.effects.setdefault(serial, {})
        outcome = next((a for a in answers if a[1] in ("ok", "fail", "beyond", "immune", "no_los", "calm_wait")), None)
        what = f"{SKILL_NAMES[sid]} on {name}"
        if outcome is None:
            log(f"{what}: no answer")
            return
        _, result, secs = outcome
        self.count(f"{EFFECT[sid]}_{result}" if result in ("ok", "fail") else f"bard_{result}")
        if result == "ok":
            fx[EFFECT[sid]] = now + secs
            if second is not None:
                self.effects.setdefault(second, {})["calm"] = now + secs
            if sid != SKILL_DISCORD:
                self.calm_ready = now + CALM_SUCCESS_S
            log(f"{what}: success ({secs:.0f} s)")
        elif result == "beyond":
            self.beyond.add((sid, type_key(name)))
            log(f"{what}: beyond our skill; not again at {type_key(name)} this run")
        elif result == "immune":
            fx["immune"] = now + BREAK_IMMUNE_S
            log(f"{what}: barding break immunity")
        elif result == "no_los":
            fx["los"] = now + LOS_BLOCK_S
            log(f"{what}: out of sight or reach")
        elif result == "calm_wait":
            self.calm_ready = now + SKILL_GAP_S
        else:
            log(f"{what}: failed")

    def all_kill(self, serial: int):
        """'All Kill' and the target cursor answered with `serial` (a mob already on us)."""
        h = self.h
        self.kill_t[serial] = time.monotonic()
        mob = (h.link.last["world"]["mobiles"].get(f"0x{serial:08X}") or {})
        h.doing("pets", f"Sending the pets at {mob.get('name') or hex(serial)}", (mob.get("x"), mob.get("y")), serial)
        mark = len(h.link.events)
        self.say("all kill")
        cur = self._cursor(mark)
        if cur is None:
            log("'All Kill': no target cursor")
            return
        if not self._aim(cur, serial, combat.VIEW_RANGE):
            return
        h.wait_for(lambda s: any(e.get("ev") == "cliloc" for e in h.link.events[mark:]), 0.6)
        if any(e.get("ev") == "cliloc" and e.get("cliloc") in NO_LOS_CLILOCS for e in h.link.events[mark:]):
            self.effects.setdefault(serial, {})["los"] = time.monotonic() + LOS_BLOCK_S
            log(f"'All Kill' at 0x{serial:08X}: cannot be seen")
        self.count("kill_commands")

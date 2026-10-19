"""Fighting with a pet team for the hunt runner (`loop_hunt.py --pets`; docs/HUNT_LOOP.md "Pets").

Learned from Logan Wolf's Mausoleum trip (capture 20261012_210013, 21:16-21:31: 25 kills, Society
job "Kill 25,000 Gold Worth" 290 -> 24,243 in 14 min; docs/NOTES.md "Logan Wolf's tamer fighting"):
a tribard tamer (Taming, Lore, Veterinary, Herding 120; Peacemaking, Discordance, Provocation 80;
Tactics 18) with a bonded phoenix (4 control slots) and a bonded gravebug (1 slot, the tank). He never
turned war mode on or swung: the pets do the killing, he keeps them alive and makes the fights easier.

- **Pets guard, the character never starts the fight** (user, 2026-10-12): a mob aggroes on what
  comes into its aggro range and line of sight; pets on "All Guard Me" engage it before it engages
  us. An aggressive act of ours (Discordance, "All Kill") makes the mob aggro on the character. So
  a target the pets haven't engaged is only approached (to APPROACH_R) and waited for, and passed over
  when it can't see us there (`unseen`); the bard skills go only at a target the pets are already
  fighting (`engaged`), and "All Kill" only at a mob already attacking us that the pets leave alone for
  KILL_AFTER_S (its aggro is on us anyway).
- **"All Guard Me"** at every arrival (guarding stops at a recall or dungeon level transition:
  wiki Animal Taming, Pet Commands) and after every kill (26 times for 25 kills on the trip; pets
  told "All Kill" go back to the owner after the kill, knowledge #2073). "All Follow Me" before
  every recall or walk out (user directive 2026-10-11, knowledge #17043: a pet was left behind),
  waiting up to FOLLOW_WAIT_S for the pets to come within FOLLOW_R.
- **Pet commands are Razor-shaped speech** (actions.razor_say: hue 0x03B1, double NUL; every pet
  command in Logan Wolf's captures; "All Patrol" his recorded macro's hue 0x0239, COMMAND_HUES) and only the
  phrases in COMMANDS. Nothing here or in `ctl` ever
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
- **Barding songs** (`--songs`, user 2026-10-13: "I forgot to buff us with our bard songs"): the same
  skill at the ground plays a song on us and the pets for SONG_S (knowledge #11233/#11234; live
  20261013_173449 all three printed 15.0 %): Discordance less damage taken from creatures, Peacemaking
  more healing received, Provocation more damage against creatures. SONG_GAP_S between songs, apart
  from the skill timer ("You must wait a few moments before performing another barding song." 3.8 s
  after one, live). Kept up whenever no other care action is due (fights included, as the user sang
  them), each again SONG_RENEW_S before it ends.
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
import os
import re
import time

import actions
import aggro
import combat
import healing
import los
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
# A channel still running (his own Razor vet loop's, live 2026-10-18 21:14:29.1-34.3: the runner's tries at 30.8 and
# 33.8 got this and each waited out the 1.5 s reply timeout, standing in a red's flight)
TEXT_ITEM_WAIT = "You must wait a few moments before you may use this item again."
# A bonded pet's ghost within reach at a channel's finish comes back (live 20261014_221341: GraveRobber died
# 22:35:53.1, 3.1 s into a channel begun before; at its finish, 2.0 s later, this line, the 0xBF sub 0x19 alive
# flag and 31 % hits, then the heal to 65 %; no gump; Logan ran with "All Follow Me" meanwhile, the ghost
# following). Knowledge #11547: pets can be resurrected with 80 Veterinary skill.
TEXT_VET_RES = "You are able to resurrect your follower."
RES_TRY_S = 30.0              # a ghost in view is vetted this long after the death before the pet's death is a leave
CLILOC_ACTION_WAIT = 500119
CLILOC_SKILL_WAIT = 500118    # "You must wait a few moments to use another skill."
CLILOC_PICK_INSTRUMENT = 500617   # "What instrument shall you play?": the one last played is gone
# lute, lap harp, standing harp, drum, tambourines (Logan's pack, live 20261015_171928: 0xEB3 his lute, 0xE9C,
# 0xE9E, 0xEB2 the lap harp after the 18:40 death)
INSTRUMENT_GRAPHICS = (0x0EB3, 0x0EB4, 0x0EB2, 0x0EB1, 0x0E9C, 0x0E9D, 0x0E9E)
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
ELSEWHERE_R = 2               # a monster in war mode this close to another player or a pet not ours fights them (elsewhere)
KILL_REPEAT_S = 12.0
IDLE_PET_S = 5.0              # a live pet out of war mode this long near a fight the other pet is in: "All Kill"
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
CHARGERS = {"nightstalker", "reanimated knight"}   # the knight: '*charges*' 3 times live, once -32 on Logan (20261014_173858)
TEXT_CHARGES = "*charges*"
CHARGE_SAFE_R = 3             # tiles from a charger and from a pet beside it: clear of where its Charge lands
# Other monsters whose hit lands on everyone near them: tiles to keep from them and the pet beside them.
# Apparition (live 20261015_090607 and _115604, test shard): its hit lands on everyone within 2 of it at once
# (action 6, ~8.8 s apart): Logan -46/-47/-48 at 0-1 and -39 at 2 (12:02:06, with the gravebug -37), the
# pets -36/-38; none on a pet at 3-5. Stand 3 off.
# Reanimated daemon (20261014_221341): its bone burst hit Logan at 5 tiles (-39/-49) and both pets (94-107).
AREA_R = {"apparition": 3, "reanimated daemon": 6}
STANDOFF_MOVES = 6
STANDOFF_REPEAT_S = 2.0
# An apparition's blink (docs/SIM.md audit, n=56): "*phases*" over it, gone (0x1D), then back 1.1-1.6 s later on its
# target's own tile with its area hit (radius 2). So on the emote, a pet it was fighting is where the hit lands: step
# AREA_R off that pet at once (user 2026-10-17: "when an apparition vanishes we know its going to pop out on the
# player/pet it was aggro'd on ... right now we just wait there to get hit for like -47"). One on us lands on us
# wherever we go: nothing to step from.
TEXT_BLINK = "*phases*"
BLINK_S = 2.0                 # the landing's tile stays a zone this long after the emote (lands 1.1-1.6 s later)
BLINK_TARGET_R = 2            # its target when no swing names it: the nearest pet or us within this of it
SWING_FRESH_S = 10.0          # a swing this recent names its target (world.swings)
# A fight with --vet-range 4+ (the Healer's Codex "Without Borders"): stand SPACING_R from the pets and from
# what fights them, every pet within --vet-range - 1 (user 2026-10-15: "Since our healing distance is 5 now I
# don't see a good reason to be standing next to the tank while it's fighting"; live 20261015_115604 12:08:53
# an apparition that had blinked out came back on the gravebug's tile and hit Logan 2 tiles off, -46).
SPACING_R = 3
SPACING_STEPS = 4
SPACING_REPEAT_S = 1.5
FIGHT_LINGER_S = 8.0          # a fight counts this long after its last monster left the view (one blinking back)

# Aggro (aggro.py): running from a --never-aggro monster in war mode, and a better tile in a fight
EVADE_CLEAR_R = 22            # run this far beyond it (live: the consort chased Logan ~25 tiles from where it woke)
EVADE_MOVES = 40
REPOSITION_S = 3.0
REPOSITION_STEPS = 2
REPOSITION_GAIN = 1.0         # a caster's sight less, or a tenth of an idle monster's weight
CASTER_R = 10                 # a caster in war mode this close in sight is casting at us (loop_hunt.CASTER_REACH)
CASTER_STEPS = 4              # ...and a reposition out of its sight goes this far
RANGED_R = 13                 # a ranged monster in war mode this close in sight is shooting at us (the purger's barrage: 12)
BACK_OFF_S = 2.0              # back_off at most this often
KEEP_OFF_S = 60.0             # ...and the purger it backed off from isn't sought this long (loop_hunt.SKIP_S)
HOLD_S = 10.0                 # hold this long for an idle monster that sees us (live: 5-10 s to war mode)
HOLD_R = 10                   # ...for one within this many tiles (the 16:12 run: none at 12-13 came)
AT_ONCE_NEAR = 6              # an "at once" stop within this of one in the last AT_ONCE_S: the way on, go
AT_ONCE_S = 120.0
PULL_STEPS = 3                # a pull spot within this many steps
# "All Patrol" at an idle corpse purger (Team.patrol_pull; live demo 2026-10-18 17:55-18:00, docs/NOTES.md "All
# Patrol"): the pets run to a ground tile and attack an idle monster 1-3 tiles from it; the purger then shoots them
PATROL_R = 14                 # a purger this close to us
PATROL_MAX_R = 11             # a spot this close to us (live: accepted at 4-11; nothing farther tried)
PATROL_SPOT_R = 2             # ...and this close to the purger (live: they took monsters 1-3 off the spot)
PATROL_CLEAR_R = 4            # ...with no other monster this close to it
PATROL_WAIT_S = 10.0          # the pets on it by then (live: 3.5 s for 11 tiles), else called back and passed over
# A forgotten soul's spell leaves fire (0x6D70, hue 0x08D8) on the tile it hit, 5.4-5.7 s (7 in capture
# 20261014_210020: 6 under the gravebug it tanked, 1 under the phoenix, at the instant of a 13-20 hit).
# None of them hurt anyone standing on or beside it (the phoenix 5.7 s on one, Logan beside it), but the
# user doesn't stand in it (2026-10-14: "learning not to stand in fire cast by a mob like the forgotten
# souls"): never a tile to step to, costly to route through, and stepped off when we stand on one.
FIRE_GRAPHICS = frozenset([0x6D70])
FIRE_STEPS = 2                # a tile out of the fire within this many steps
BACKSPAWN_MERGE = 3           # stealther spawn spots closer than this are one (Team.backspawn)
SPAWNS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sim", "mausoleum.json")

# The only phrases ever said to the pets, as Logan Wolf's hotkeys say them: Razor's built-in Say hotkeys in its hue
# 0x03B1; "All Patrol" his recorded macro (Shift+S), in the hue it was recorded in (docs/NOTES.md "All Patrol")
COMMANDS = {c.lower(): c for c in ("All Guard Me", "All Follow Me", "All Kill", "All Stop", "All Stay", "All Come",
                                   "All Patrol")}
COMMAND_HUES = {"All Patrol": 0x0239}
# Anything that gives a pet away: "<name> release", "all release", the context menu's Release/Transfer
IRREVERSIBLE = re.compile(r"\b(release|transfer)\b", re.I)
ANSWER_OK = re.compile(r"^You play successfully, (disrupting|pacifying|provoking)")
ANSWER_FAIL = re.compile(r"^You fail to (discord|pacify|incite)")
DURATION = re.compile(r"\((?:(\d+)m)?\s*(?:(\d+)s)?\)\s*$")
TEXT_CALM_WAIT = "You must wait a few moments before you may provoke or pacify another creature."
# Area Peacemaking: the skill's cursor answered with ourselves (user 2026-10-15: "in a jam you can peace the area
# around you by activating Peacemaking and targeting yourself ... Good if you are walking through a group of mobs or
# get ganged up on"; wiki Peacemaking "Area Peacemaking": a check per non-tamed creature within 8 tiles, 2 s
# pacified each, the usual cooldowns; live 20261015_171928 17:22:01 the user's own: this text, then the calm wait)
TEXT_AREA_PEACE_OK = "You play successfully, briefly pacifying one or more nearby creatures."
AREA_PEACE_R = 8
JAM_R = 3                     # monsters in war mode this close: a jam (two on us, or one on us when we're low)
TEXT_IMMUNE = "That target is currently under a barding break effect."
TEXT_BEYOND = "beyond your skill"
TEXT_INCITE_SECOND = "Whom do you wish them to attack?"
SONG_ENDS = re.compile(r"^Your Song of (Discordance|Peacemaking|Provocation) effect ends\.")   # live 20261014_170144
TEXT_POISONED = "*looks violently ill*"
OK_SKILL = {"disrupting": SKILL_DISCORD, "pacifying": SKILL_PEACE, "provoking": SKILL_PROVO}
FAIL_SKILL = {"discord": SKILL_DISCORD, "pacify": SKILL_PEACE, "incite": SKILL_PROVO}
EFFECT = {SKILL_DISCORD: "discord", SKILL_PEACE: "calm", SKILL_PROVO: "calm"}
DEFAULT_S = {SKILL_DISCORD: 60.0, SKILL_PEACE: 30.0, SKILL_PROVO: 30.0}   # when the answer names no duration
SONG_ORDER = (SKILL_DISCORD, SKILL_PEACE, SKILL_PROVO)   # live order; damage taken first
SONG_NAMES = {SKILL_DISCORD: "Discordance", SKILL_PEACE: "Peacemaking", SKILL_PROVO: "Provocation"}
SONG_ON = re.compile(r"^You are now under the effect of a Song of (Discordance|Peacemaking|Provocation)\b")
TEXT_SONG_WAIT = "You must wait a few moments before performing another barding song."
SONG_BUFF_ICONS = {SKILL_DISCORD: 59, SKILL_PEACE: 58, SKILL_PROVO: 57}   # our buffs (live 20261015_115604 status)
SONG_S = 900.0                # 15 minutes (knowledge #9034, live "for 15 minutes")
SONG_GAP_S = 10.0             # between songs (knowledge #11234)
SONG_RENEW_S = 60.0


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
    return actions.razor_say(phrase, hue=COMMAND_HUES.get(phrase, actions.RAZOR_SPEECH_HUE))


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


def song_answer(e: dict):
    """("on", skill id) for a song that started, ("wait", None) for the song cooldown, ("fail", skill id)
    for a fail line, ("skill_wait", None) for the skill timer, else None."""
    if e.get("ev") == "cliloc" and e.get("cliloc") == CLILOC_SKILL_WAIT:
        return "skill_wait", None
    if e.get("ev") != "speech_heard":
        return None
    text = e.get("text") or ""
    if m := SONG_ON.match(text):
        return "on", {v: k for k, v in SONG_NAMES.items()}[m.group(1)]
    if text == TEXT_SONG_WAIT:
        return "wait", None
    if m := ANSWER_FAIL.match(text):
        return "fail", FAIL_SKILL[m.group(1)]
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


class ClearOf:
    """Goal predicate for the Mover: at least its own distance from every tile of an area_zone
    ({(x, y): r}); admissible heuristic as nav.beyond's."""

    __slots__ = ("zone", "z_ok")

    def __init__(self, zone: dict):
        self.zone = dict(zone)
        self.z_ok = None

    def __call__(self, t) -> bool:
        return all(cheb(t, c) >= r for c, r in self.zone.items())

    def heuristic(self, t) -> int:
        return max((max(0, r - cheb(t, c)) for c, r in self.zone.items()), default=0)



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
        self.no_instrument = False       # the instrument prompt came and none is in the pack: no bard skills
        self.skill_ready = 0.0           # any bard skill
        self.calm_ready = 0.0            # peace / provoke
        self.effects = {}                # serial -> {"discord": until, "calm": until, "immune": until, "los": until}
        self.beyond = set()              # (skill, creature type) failed "beyond your skill"
        self.kill_t = {}                 # serial -> our last "All Kill" at it
        self.attack_t = {}               # serial -> first seen attacking us (this engagement)
        self.blinks = {}                 # apparition serial -> (monotonic time of its "*phases*", target serial)
        self.poisoned_t = {}             # pet serial -> "*looks violently ill*" over it
        self.seen_t = {}                 # pet serial -> last seen in view
        self.seen_at = {}                # pet serial -> (x, y) where last seen alive (rejoin)
        self.walk_pet_t = 0.0
        self.retreat_t = -1e9
        self.counts = {}
        self.fought = {}                 # serial -> (last-seen mob dict, monotonic): mobs the pets fought
        self.chargers = set(CHARGERS)    # creature types with an area Charge (known + seen this run)
        self.hold_until = {}             # serial -> until when we hold for it to come (pull_check)
        self.never_last = None           # (serials, nearest) of the --never-aggro monsters in sight at the last pull_check
        self.spot_for = frozenset()      # the monsters the last pull spot was chosen for
        self.standoff_t = -1e9
        self.standoff_stay = None        # why the last stand-off stayed put (logged once)
        self.songs = {}                  # song skill -> monotonic time it runs out
        self.at_once_w = 0.0             # idle weight in sight at the last pull_check (its "at once" rule)
        self.at_once_at = []             # [((x, y), monotonic)] where "at once" stopped a patrol lately
        self.idle_since = {}             # pet serial -> monotonic time first seen out of war mode near a fight
        self.song_ready = 0.0
        self._view = None                # aggro.View of the last state read
        self.pull_why = None
        self.evading = False
        self.reposition_t = -1e9
        self.back_off_t = -1e9            # the last back_off (out from before a shy ranged monster)
        self.kept_off = {}                # serial -> monotonic: a purger back_off stepped away from, not sought until
        self.patrol = None                # the "All Patrol" out (patrol_pull): serial, kind, spot, t
        self.patrol_failed = {}           # serial -> monotonic: no patrol at it until then (refused, timed out)
        self.bard_step_t = -1e9           # the last bard_step (a step to see a bard target)
        self.spacing_t = -1e9
        self.fight_t = -1e9               # the last time the pets were seen fighting (spacing)
        self.tank_t = {}                 # pet serial -> the last time it stood next to a monster in war mode
        self.dead_since = {}             # pet serial -> monotonic time its ghost was first seen
        self.follow_needed = False       # a pet died: "All Follow Me" so the ghost stays in the vet's reach
        self.ghosts = set()              # pet serials seen dead (update: the "All Follow Me" once per death)
        self.backspawn = {}              # (x, y) -> stealther type: its spawn spot (seed_backspawn, sightings)
        self.sighted = set()             # mobiles already looked at for a first sighting

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
        self.seed_backspawn(st)

    def seed_backspawn(self, st):
        """Stealthers' spawn spots (aggro.stealthy) on this facet: where the store saw us kill one (our tile then,
        within the pull range of it) and the Mausoleum's observed first sightings (harness/data/sim/mausoleum.json
        spawns: every first-seen position in the captures). One spot per BACKSPAWN_MERGE tiles."""
        facet = (st["world"].get("self") or {}).get("map") or 0
        pos = self.h.pos(st)
        found = []
        mem = getattr(self.h, "memory", None)
        if mem is not None and hasattr(mem, "job_events"):
            for e in mem.job_events("hunt", since=0.0):
                if e["kind"] != "kill" or e.get("x") is None or (e.get("facet") or 0) != facet:
                    continue
                data = e["data"] if isinstance(e["data"], dict) else json.loads(e["data"] or "{}")
                kind = type_key(data.get("name"))
                if aggro.stealthy(kind):
                    found.append(((e["x"], e["y"]), kind))
        try:
            with open(SPAWNS_PATH, encoding="utf-8") as f:
                for s in json.load(f).get("spawns") or []:
                    if aggro.stealthy(s["key"]):
                        found.append((tuple(s["at"][:2]), s["key"]))
        except OSError:
            pass
        for spot, kind in found:
            if cheb(spot, pos) <= 200:
                self.add_backspawn(spot, kind)
        if self.backspawn:
            log(f"backspawn: {len(self.backspawn)} stealther spawn spot(s) known here; standing out of their open ground")

    def add_backspawn(self, spot, kind):
        if not any(cheb(spot, s) <= BACKSPAWN_MERGE for s in self.backspawn):
            self.backspawn[tuple(spot)] = kind

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
            if not m.get("dead"):
                self.seen_at[s] = (m["x"], m["y"])
            out.append({"serial": s, "name": name, "frac": self.h.frac(m), "dist": cheb(pos, (m["x"], m["y"])),
                        "dead": bool(m.get("dead")), "x": m["x"], "y": m["y"], "war": bool((m.get("flags") or 0) & aggro.WAR),
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
                elif text == TEXT_VET_BEGIN:
                    # ours, or his own Razor vet loop's (live 2026-10-18 21:14:14.0 and 21:14:29.1): no try meanwhile
                    self.vet_busy_until = max(self.vet_busy_until, now + VET_BUSY_S)
                elif m := SONG_ENDS.match(text):
                    sid = {v: k for k, v in SONG_NAMES.items()}[m.group(1)]
                    self.songs[sid] = 0.0                   # live: 15 min 4 s after it started
            elif kind == "speech_heard" and s is not None:
                if s in self.roster and text == TEXT_POISONED:
                    self.poisoned_t[s] = now
                elif text == TEXT_CHARGES and s not in self.roster:
                    m = st["world"]["mobiles"].get(f"0x{s:08X}") or {}
                    kind = type_key((st["world"].get("labels") or {}).get(f"0x{s:08X}") or m.get("name"))
                    if kind and kind not in self.chargers:
                        self.chargers.add(kind)
                        log(f"{kind} charges: standing {CHARGE_SAFE_R} tiles off it and the pet it fights")
                elif text == TEXT_BLINK and s not in self.roster:
                    target = self.blink_target(st, s)
                    self.blinks[s] = (now, target)
                    if target in self.roster:
                        log(f"0x{s:08X} phases: it lands on {self.roster[target]} in ~1.5 s")
                elif "barding break" in text.lower() and s not in self.roster:
                    fx = self.effects.setdefault(s, {})
                    fx["calm"], fx["immune"] = 0.0, now + BREAK_IMMUNE_S
                    log(f"barding break on 0x{s:08X}: no peace or provocation at it for {BREAK_IMMUNE_S:.0f} s")
            elif kind == "mobile_death" and s is not None:
                self.effects.pop(s, None)
                self.attack_t.pop(s, None)
        self.ev_upto = len(evs)
        for s in [s for s, (t, _) in self.blinks.items() if now - t > BLINK_S]:
            del self.blinks[s]
        for p in self.pets(st):
            s = p["serial"]
            if p["dead"] and s not in self.ghosts:
                self.ghosts.add(s)
                self.dead_since.setdefault(s, now)
                self.follow_needed = True
                log(f"{p['name']} is dead: its ghost follows us to be resurrected by the vet supplies")
            elif not p["dead"] and s in self.dead_since:
                self.ghosts.discard(s)
                del self.dead_since[s]
                self.guard_needed = True
                self.count("pet_res")
                log(f"{p['name']} is alive again ({p['frac']:.0%})")
        fire = self.fire_tiles(st)
        mv = self.h.mover
        for k in [k for k in mv.danger if isinstance(k, str) and k.startswith("fire:")
                  and mv.danger[k][0] not in fire]:
            del mv.danger[k]
        for t in fire:
            mv.danger.setdefault(f"fire:{t[0]},{t[1]}", (t, 0))   # routes step round it (Mover.danger_cost)
        labels = st["world"].get("labels") or {}
        for key, mob in st["world"]["mobiles"].items():
            s = serial_of(key)
            if s in self.sighted or s in self.roster or mob.get("x") is None or mob.get("pet"):
                continue
            self.sighted.add(s)
            kind = type_key(labels.get(key) or mob.get("name"))
            if kind and aggro.stealthy(kind) and not (mob.get("flags") or 0) & aggro.WAR:
                self.add_backspawn((mob["x"], mob["y"]), kind)        # idle where we first saw it: its spawn

    @staticmethod
    def fire_tiles(st) -> set:
        """(x, y) of the fire on the ground (FIRE_GRAPHICS) now."""
        return {(it["x"], it["y"]) for it in st["world"]["items"].values()
                if it.get("graphic") in FIRE_GRAPHICS and it.get("container") is None and it.get("x") is not None}

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

    def elsewhere(self, st, mob: dict) -> str | None:
        """Who `mob` fights when it's someone else's fight: in war mode with another player, or a pet not ours,
        within ELSEWHERE_R (user 2026-10-17: "The runner seems not to have a concept of mobs that are fighting
        other players (and their pets). It 'waits for them to come on' but the mob is already aggro'd on something
        else"). The name of the nearest such mobile, else None."""
        import threats
        if not (mob.get("flags") or 0) & threats.FLAG_WARMODE or mob.get("x") is None:
            return None
        world, me = st["world"], self.h.me(st)
        labels = world.get("labels") or {}
        best = None
        for key, o in world["mobiles"].items():
            s = serial_of(key)
            if s == me or s in self.roster or o.get("x") is None or o.get("dead"):
                continue
            d = cheb((o["x"], o["y"]), (mob["x"], mob["y"]))
            if d > ELSEWHERE_R or (best is not None and d >= best[0]):
                continue
            kind, player, _ = threats.identify(o, labels.get(key))
            if player or o.get("pet"):
                best = (d, labels.get(key) or o.get("name") or f"0x{s:08X}")
        return best[1] if best else None

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
        """(why, severity) to leave for the pets' sake, else None. --heal-in-place: a pet below
        --pet-leave-at is healed where it stands while vet supplies last (user 2026-10-13)."""
        now = time.monotonic()
        seen = {p["serial"]: p for p in self.pets(st)}
        for s, name in self.roster.items():
            p = seen.get(s)
            if p is None:
                if now - self.seen_t.get(s, now) > PET_MISSING_S:
                    return f"{name} out of view for {PET_MISSING_S:.0f} s", "attention"
                continue
            if p["dead"]:
                if now - self.dead_since.setdefault(s, now) <= RES_TRY_S \
                        and healing.in_pack(st["world"], self.h.me(st), VET_SUPPLIES_GRAPHIC):
                    continue                              # vet supplies resurrect its ghost (TEXT_VET_RES)
                return f"{name} is dead", "attention"
            if p["frac"] < self.a.pet_leave_at and not (
                    self.a.heal_in_place and healing.in_pack(st["world"], self.h.me(st), VET_SUPPLIES_GRAPHIC)):
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
        self.h.human.wait("hotkey")       # a Razor "Say" hotkey (humanize SCRIPT_MEDIAN)
        self.h.link.act(command(text))
        self.count("commands")
        log(f'said "{COMMANDS[text.lower()]}"')
        return True

    def busy_healing(self, st) -> bool:
        """Don't walk on yet: a vet channel runs (a step shortens its reach to 1, wiki Veterinary), a pet in
        reach still needs one (vet_needed) or a pet's ghost waits for one, and supplies are there. Moving on at
        once from a cleared area (--crawl-depleted-s 5) otherwise left with a hurt gravebug and broke channels
        mid-way."""
        if time.monotonic() < self.vet_busy_until:
            return True
        return (self.vet_needed(st, self.pets(st)) is not None or bool(self.dead_since)) \
            and bool(healing.in_pack(st["world"], self.h.me(st), VET_SUPPLIES_GRAPHIC))

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

    def follow(self, severity: str = "info", wait: bool = True):
        """'All Follow Me' before a recall or a walk out, then up to FOLLOW_WAIT_S[severity] for every pet
        (a ghost too: it comes along) to come within FOLLOW_R (user directive 2026-10-11); hurt
        ("attention" below --heal-at): fall back from the attackers meanwhile instead of standing.
        `wait` False: no wait and no fall back (hurt beside the gate: live 20261015_090607 Logan stood 7 s
        beside it for pets fighting 20 tiles off and a banshee killed him; user 2026-10-15: "its fine if our
        pets stay or die in the dungeon, we can easily retrieve them at the stablemaster"). The waits read
        the state through the host (HuntLoop.state: its guards drink a potion on the way out)."""
        self.say("all follow me")
        st = self.h.state()
        me = st["world"]["self"]
        hurt = me.get("hits") is not None and self.h.frac(me) < self.a.heal_at
        if severity == "attention" and not hurt:
            severity = "info"                # leaving for a pet's sake, we aren't hurt: wait for it in full
        if wait:
            if severity == "attention":
                self.fall_back(st)
            end = time.monotonic() + FOLLOW_WAIT_S.get(severity, FOLLOW_WAIT_S["info"])
            while time.monotonic() < end and not self.together(self.h.state()):
                time.sleep(0.4)
        far = [f"{p['name']} at {p['dist']}" for p in self.pets(self.h.link.state()) if p["dist"] > FOLLOW_R]
        if far:
            log("pets not all within reach after 'All Follow Me': " + ", ".join(far)
                + ("" if wait else "; not waiting (hurt)"))

    def together(self, st, at=None) -> bool:
        """Every pet of the roster in view within FOLLOW_R of us, or of tile `at` (a teleporter we step
        onto next: the pets that come along are counted from it [INFERENCE: ServUO TeleportPets from the
        mover's location]; a pet 3 tiles behind us was 4 from it and stayed behind in the simulator)."""
        near = {p["serial"] for p in self.pets(st)
                if (p["dist"] if at is None else cheb((p["x"], p["y"]), at)) <= FOLLOW_R}
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
            while time.monotonic() < end and not self.together(h.state()):
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
        """One care action, True when something was done: evade a --never-aggro monster, "All Follow Me" after
        a pet's death (its ghost stays near for the vet supplies), step out of fire, retreat, vet supplies on us
        when we are below --heal-at, keep off the tank, vet supplies, guard, walk to a hurt pet or a ghost, a
        better tile for line of sight, a barding song. Standing off an area hitter comes before this and the
        potion (HuntLoop.tick)."""
        self.update(st)
        me = st["world"]["self"]
        low = me.get("hits") is not None and self.h.frac(me) < self.a.heal_at
        return self.evade(st) or (self.follow_needed and self.ghost_follow()) or self.out_of_fire(st) \
            or self.area_peace(st) or self.retreat(st) or (low and self.vet(st)) or self.spacing(st) or self.vet(st) \
            or (self.guard_needed and self.guard()) or self.to_pet(st) or self.reposition(st) or self.song(st)

    def spacing(self, st) -> bool:
        """In a fight (aggro.View.engaged, or one within FIGHT_LINGER_S) with --vet-range SPACING_R + 1 or
        more: when a pet or a monster in war mode is within SPACING_R - 1 of us, step (up to SPACING_STEPS)
        to the tile the fewest idle monsters see that is SPACING_R from every pet and every monster in war
        mode and within --vet-range - 1 of every pet, out of area hits and fire; never mid-channel."""
        h, now = self.h, time.monotonic()
        if self.a.vet_range < SPACING_R + 1 or now < self.vet_busy_until or now - self.spacing_t < SPACING_REPEAT_S:
            return False
        v = self.view(st)
        if v.walk is None:
            return False
        war = [m["pos"][:2] for m in v.mobs if m["war"]]
        if v.engaged():
            self.fight_t = now
        if now - self.fight_t > FIGHT_LINGER_S:
            return False
        pets = [p for p in self.pets(st) if not p["dead"] and p["dist"] <= self.a.vet_range + 2]
        for p in pets:                                  # a tank: next to a monster in war mode lately
            if any(cheb((p["x"], p["y"]), w) <= PET_NEAR + 1 for w in war):
                self.tank_t[p["serial"]] = now
        tanks = [(p["x"], p["y"]) for p in pets if now - self.tank_t.get(p["serial"], -1e9) <= FIGHT_LINGER_S]
        pets = [(p["x"], p["y"]) for p in pets]
        near = tanks + war
        if not tanks or all(cheb(v.pos, t) >= SPACING_R for t in near):
            return False
        self.spacing_t = now
        zone, fire = self.area_zone(st), self.fire_tiles(st)
        clear = ClearOf(zone)
        best, best_c = None, None
        for t, k in self.tiles_within(v, SPACING_STEPS).items():
            if k == 0 or t[:2] in fire or not clear(t) or any(cheb(t, n) < SPACING_R for n in near) \
                    or any(cheb(t, p) > self.a.vet_range - 1 for p in pets):
                continue
            c = (round(v.stand_score(t)), k)
            if best_c is None or c < best_c:
                best, best_c = t, c
        if best is None:
            return False
        label = "off the tank"
        h.doing("reposition", f"Stepping off the tank to {best[0]},{best[1]}", best[:2])
        self.count("spacing")
        log(f"keeping off the tank: {best[:2]}, {SPACING_R}+ from the pets and what fights them")
        try:
            h.mover.walk_to(lambda: best[:2], 0, label, max_moves=2 * SPACING_STEPS, max_route=2 * SPACING_STEPS)
        except Abort as e:
            if not str(e).startswith(label):
                raise
            log(f"off the tank: {e}")
        return True

    def ghost_follow(self) -> bool:
        self.follow_needed = False
        return self.say("all follow me")

    # ------------------------------------------------------------ aggro (aggro.py)
    def view(self, st) -> aggro.View:
        """An aggro.View of this state read (one per read: line of sight is cached in los.Sight)."""
        if self._view is None or self._view.st is not st:
            self._view = aggro.View(self.h, st)
        return self._view

    def pull_check(self, st) -> str | None:
        """Why an advance (a patrol walk, an approach) should stop here: aggro.View.overpull, a --never-aggro
        monster or an idle corpse purger (aggro.SHY) that would see us before a pet (back_off steps out), or
        hold_reason. Its "at once" (more than the budget idle in sight together) only on a step that adds to
        what sees us: walking out of such a spot goes on (sim fight1949: stopped at every step out, stuck)."""
        budget = self.a.pull_budget
        v = self.view(st)
        why = v.overpull(budget) if budget > 0 else (f"a {v.never_near(v.pos)[0]['kind']} would see us"
                                                     if v.never_near(v.pos) else None)
        shy = self.shy_first(v, v.pos)
        if not why and shy:
            why = f"a {shy[0]['kind']} would see us"
        w = sum(m["weight"] for m in v.wakes(v.pos))
        now = time.monotonic()
        if why and "at once" in why and w <= self.at_once_w + 1e-6:
            why = None
        elif why and "at once" in why and any(cheb(v.pos, p) <= AT_ONCE_NEAR and now - t < AT_ONCE_S
                                              for p, t in self.at_once_at):
            # stopped here already and the patrol came back this way: no other way on; go (live 20261015_210709
            # 21:11:13-19: stop, "patrolling elsewhere", the next waypoint's route through the same spot, stop...)
            why = None
        elif why and "at once" in why:
            self.at_once_at = [(p, t) for p, t in self.at_once_at if now - t < AT_ONCE_S] + [(v.pos[:2], now)]
        self.at_once_w = w
        seers = v.never_near(v.pos) + shy if why and why.endswith("would see us") else []
        if seers:
            # a --never-aggro monster in sight (or a purger before the pets): stop only on a step that brings a new one
            # into sight or goes nearer; walking out of its sight goes on (live 2026-10-17 21:12-21:16: 3225 "stopped (a
            # elder vampire would see us)" lines, the patrol stopped before every first step and the runner stood in its
            # sight)
            seen = (frozenset(m["serial"] for m in seers), min(cheb(m["pos"], v.pos) for m in seers))
            last, self.never_last = self.never_last, seen
            if last is not None and seen[0] <= last[0] and seen[1] >= last[1]:
                why = None
        else:
            self.never_last = None
        why = why or self.hold_reason(st)
        if why and why != self.pull_why:
            self.count("pull_holds")
            log(f"holding here: {why}")
        self.pull_why = why
        return why

    def hold_reason(self, st) -> str | None:
        """--hold-first-sight, nothing fighting: an idle monster that sees us within its aggro range (AGGRO_R
        13, the user's "12 or 13"); hold up to HOLD_S and let it come (Logan at the nightstalker room's north
        edge, 20261013_204518: the wolves and the stalker came 5-10 s after first sight, the knight only when he
        moved on); pull_spot picks where to stand for it. A monster held for that long without coming is
        passed over. Live 20261015_210709 21:34:05-15 a shadowguard 14 tiles off (a tile beyond the aggro range,
        the old margin) held the patrol 10 s and never woke (the user: "I'm pretty sure aggro range smaller
        than 14 tiles"). Only one that sees us past the walls too (a Wall-flagged static blocks: aggro.View.wakes
        strict) and within HOLD_R (user 2026-10-17: "Logan is waiting for mobs that can't see him through walls
        to 'come on'"; live 16:12 run, 17 holds: 11 of the 13 within 10 tiles woke in 0.5-3.5 s, two not found in
        the replay; a shadow minion 12 tiles off behind a Wall-only piece and two at 13 never did; the Wall rule
        would also have passed over the 16:38:21 shadowguard, which woke in 0.7 s)."""
        v = self.view(st)
        if not self.a.hold_first_sight or v.engaged():
            return None
        now = time.monotonic()
        seen = []
        for m in v.wakes(v.pos, margin=0, strict=True):
            if m["shy"]:
                continue                  # it shoots from where it stands, it doesn't come on (back_off)
            if cheb(m["pos"], v.pos) <= HOLD_R and self.hold_until.setdefault(m["serial"], now + HOLD_S) > now:
                seen.append(m)
        if not seen:
            return None
        m = min(seen, key=lambda m: cheb(m["pos"], v.pos))
        return f"{m['kind']} in sight at {cheb(m['pos'], v.pos)} tiles: letting it come"

    def shy_first(self, v, tile) -> list[dict]:
        """Idle corpse purgers (aggro.SHY) that would see us on `tile` before a pet (aggro.View.first_on_us)."""
        return [m for m in v.first_on_us(tile, margin=1) if m["shy"]]

    def back_off(self, st) -> bool:
        """An idle corpse purger (aggro.SHY) would see us before a pet (aggro.View.first_on_us): its first sight puts
        its aggro on us and its barrage reaches us wherever the pets stand, so step (up to PULL_STEPS, at most every
        BACK_OFF_S) to a tile out of its sight, else farther from it than here, keeping the pets within --vet-range:
        the guarding pets trail us, so it meets them first. User 2026-10-17: "We're frequently getting diseased by
        the corpse purgers"; all 4 purger diseases that day came right after "holding here: corpse purger in sight
        at 10 tiles: letting it come" (live 23:14:08-12: it woke at 9 with Logan the nearest of us, barraged him -23,
        -26 with "You have contracted a foul disease!", the gravebug beside it). It isn't held for (hold_reason: it
        doesn't come on), an advance stops on a step into its sight before the pets (pull_check), and once backed off
        from, the patrol doesn't seek it for KEEP_OFF_S (crawl.Crawl.seek); live 2026-10-18 12:05:09-25 back off, then
        the patrol or an approach on into its sight, "letting it come", back off, four times, then its barrage. True
        when we stepped."""
        h, now = self.h, time.monotonic()
        if now - self.back_off_t < BACK_OFF_S:
            return False
        v = self.view(st)
        first = self.shy_first(v, v.pos)
        if v.walk is None or not first:
            return False
        self.back_off_t = now
        self.kept_off.update((m["serial"], now + KEEP_OFF_S) for m in first)
        m = min(first, key=lambda m: cheb(m["pos"], v.pos))
        here = cheb(m["pos"], v.pos)
        keep = [(p["x"], p["y"]) for p in self.pets(st) if not p["dead"] and p["dist"] <= self.a.vet_range + 1]
        zone, fire = self.charge_zone(st), self.fire_tiles(st)
        best, best_k = None, None
        for t, k in self.tiles_within(v, PULL_STEPS).items():
            if k == 0 or t[:2] in zone or t[:2] in fire or v.never_near(t) \
                    or any(cheb(t, p) > self.a.vet_range for p in keep):
                continue
            seen, d = v.sees(m, t, m["r"] + 1, strict=True), cheb(m["pos"], t)
            if seen and d <= here:
                continue
            key = (seen, -d, v.stand_score(t), k)
            if best_k is None or key < best_k:
                best, best_k = t, key
        if best is None:
            return False
        label = "back behind the pets"
        h.doing("hold", f"Stepping back to {best[0]},{best[1]}: the pets first in the {m['kind']}'s sight", best[:2])
        self.count("back_offs")
        log(f"{m['kind']} 0x{m['serial']:08X} would see us before the pets at {here} tiles (ranged: its first sight "
            f"takes us); back to {best[:2]}" + (" out of its sight" if not best_k[0] else ""))
        try:
            h.mover.walk_to(lambda: best[:2], 0, label, urgent=True, max_moves=2 * PULL_STEPS, max_route=2 * PULL_STEPS)
        except Abort as e:
            if not str(e).startswith(label):
                raise
            log(f"back off: {e}")
        return True

    def patrol_pull(self, st, serial: int | None = None) -> bool:
        """--patrol-pull: "All Patrol" at a ground tile beside an idle corpse purger (aggro.SHY) within PATROL_R, so
        the pets wake it and its barrage goes to them (user 2026-10-18: "We can send our pets to a spot away from us,
        e.g. to within aggro distance of a purger, while we're not in aggro range"; live demo 17:55-18:00, docs/NOTES.md
        "All Patrol": both purgers woken that way shot the pets, Logan 100/100). Only with nothing fighting, nothing
        on us, every pet alive in view within --vet-range, and the purger not seeing us before the pets (back_off
        goes first). The spot: patrol_spot. `serial`: only that purger. True when the command went out (patrol_watch
        takes it from there) or its cursor failed."""
        if not getattr(self.a, "patrol_pull", False) or self.patrol is not None:
            return False
        h, now = self.h, time.monotonic()
        v = self.view(st)
        if v.walk is None or v.engaged() or h.attackers(st) or self.shy_first(v, v.pos):
            return False
        pets = [p for p in self.pets(st) if not p["dead"]]
        if len(pets) < len(self.roster) or any(p["dist"] > self.a.vet_range for p in pets):
            return False
        best = None
        for m in sorted(v.mobs, key=lambda m: cheb(m["pos"], v.pos)):
            if not m["shy"] or m["war"] or (serial is not None and m["serial"] != serial) \
                    or cheb(m["pos"], v.pos) > PATROL_R or self.patrol_failed.get(m["serial"], 0) > now \
                    or h.skip.get(m["serial"], 0) > now:
                continue
            if (spot := self.patrol_spot(v, m)) is not None:
                best = (m, spot)
                break
        if best is None:
            return False
        m, (tx, ty, tz) = best
        s = m["serial"]
        h.doing("pets", f"Sending the pets to patrol by the {m['kind']}", (tx, ty), s)
        mark = len(h.link.events)
        self.say("all patrol")                # his Razor macro on Shift+S: a hotkey's pace
        cur = self._cursor(mark)
        if cur is None:
            log("'All Patrol': no target cursor")
            self.patrol_failed[s] = now + SKIP_S
            return True
        h.human.wait("hotkey_aim")           # live: the click 0.3-0.8 s after the cursor
        if not h.cursor_still(h.state(), cur):
            log("'All Patrol': the cursor went away")
            return True
        h.link.act(actions.target_xyz(cur["cursor_id"], tx, ty, tz, 0, cur.get("cursor_type") or 0))
        h.wait_for(lambda _: any(e.get("ev") == "cliloc" and e.get("cliloc") in NO_LOS_CLILOCS
                                 for e in h.link.events[mark:]), 0.6)
        if any(e.get("ev") == "cliloc" and e.get("cliloc") in NO_LOS_CLILOCS for e in h.link.events[mark:]):
            log(f"'All Patrol' to {(tx, ty)}: cannot be seen; not trying the {m['kind']} again for {SKIP_S:.0f} s")
            self.patrol_failed[s] = now + SKIP_S
            self.count("patrol_refused")
            return True
        self.patrol = {"serial": s, "kind": m["kind"], "spot": (tx, ty), "t": time.monotonic()}
        self.count("patrols")
        log(f"'All Patrol' to {(tx, ty)}, {cheb(m['pos'], (tx, ty))} from the {m['kind']} 0x{s:08X} at "
            f"{cheb(m['pos'], v.pos)} tiles: the pets wake it, not us")
        return True

    def patrol_spot(self, v, m) -> tuple | None:
        """Where to send the pets for idle monster `m` (patrol_pull): a standable tile within PATROL_SPOT_R of it and
        PATROL_MAX_R of us, in our sight (eye to the ground: the server answers a tile out of it with 500237 "Target
        cannot be seen."; los.Sight agreed on all 8 of the demo's clicks), no other monster within PATROL_CLEAR_R
        (the pets take on what they find there) or seeing where we'll stand to vet them (sees_fight), and no
        --never-aggro one seeing it. The nearest to it, then to us. (x, y, z) or None."""
        mx, my, mz = m["pos"]
        x, y, z = v.pos
        best, best_k = None, None
        for dx in range(-PATROL_SPOT_R, PATROL_SPOT_R + 1):
            for dy in range(-PATROL_SPOT_R, PATROL_SPOT_R + 1):
                t = (mx + dx, my + dy)
                if t == (mx, my) or cheb(t, v.pos) > PATROL_MAX_R:
                    continue
                k = (max(abs(dx), abs(dy)), cheb(t, v.pos))
                if best_k is not None and k >= best_k:
                    continue
                tz = v.walk.new_z(t[0], t[1], mz, 0)
                if tz is None or (v.sight is not None
                                  and v.sight.point_blocked((x, y, z + los.EYE), (t[0], t[1], tz)) is not None):
                    continue
                spot = (t[0], t[1], tz)
                if any(o["serial"] != m["serial"] and (cheb(o["pos"], t) <= PATROL_CLEAR_R
                                                       or self.sees_fight(v, o, spot)) for o in v.mobs) \
                        or v.never_near(spot):
                    continue
                best, best_k = (t[0], t[1], tz), k
        return best

    def sees_fight(self, v, o, spot) -> bool:
        """Whether idle monster `o` would wake to us vetting the pets at `spot`: in sight of it within its aggro range
        plus --vet-range (we walk to a hurt pet: to_pet). The simulator, the first patrol rule (another monster only
        not within PATROL_CLEAR_R of the spot): all 1213 of the purger damage on Logan came from a second purger, never
        the one patrolled at, as he walked in to vet the pets."""
        return not o["war"] and v.sees(o, spot, o["r"] + self.a.vet_range)

    def patrol_watch(self, st) -> bool:
        """After patrol_pull: "All Guard Me" once the purger is awake with a pet in war mode near it (a fight they're
        in goes on: live 17:56:36 and 17:58:03 they fought on to the kill), at once when something is on us (pets on
        patrol don't guard us: live 18:00:26 a second tormented soul on Logan, 100 -> 39), and after PATROL_WAIT_S
        without it (the purger passed over for SKIP_S). Meanwhile we stand. True while it holds the tick."""
        p = self.patrol
        if p is None:
            return False
        h, now = self.h, time.monotonic()
        mob = st["world"]["mobiles"].get(f"0x{p['serial']:08X}")
        gone = mob is None or mob.get("x") is None or p["serial"] in h.dead
        awake = not gone and bool((mob.get("flags") or 0) & aggro.WAR)
        on_it = awake and any(q["war"] and not q["dead"] and cheb((q["x"], q["y"]), (mob["x"], mob["y"])) <= PET_REACH
                              for q in self.pets(st))
        late = now - p["t"] > PATROL_WAIT_S
        if h.attackers(st) or gone or on_it or late:
            self.patrol = None
            why = ("something on us" if h.attackers(st) else "it's gone" if gone else "the pets are on it" if on_it
                   else f"the pets not on it after {PATROL_WAIT_S:.0f} s")
            if late and not on_it:
                self.patrol_failed[p["serial"]] = now + SKIP_S
                self.count("patrol_timeouts")
            log(f"patrol at the {p['kind']} 0x{p['serial']:08X}: {why}; 'All Guard Me'")
            self.say("all guard me")
            return True
        h.doing("pets", f"The pets are patrolling to the {p['kind']}", p["spot"], p["serial"])
        time.sleep(0.2)
        return True

    def pull_spot(self, st) -> bool:
        """Holding (hold_reason): once per group of monsters held for, the tile within PULL_STEPS where the
        fewest of them would see us before a pet (aggro.View.first_on_us: out of their sight, or the pets
        nearer them; user 2026-10-15: "position yourself so the pets are between you and the mob, or ... out
        of LoS"), then the fewest (by aggro weight) within their range, but at least one near enough to see
        the pets beside us, so it comes, to the pets, alone or with the fewest; not in a Charge's reach or a
        --never-aggro monster's sight. True when we stepped there."""
        h = self.h
        v = self.view(st)
        if v.walk is None:
            return False
        now = time.monotonic()
        group = frozenset(m["serial"] for m in v.wakes(v.pos, margin=0) if self.hold_until.get(m["serial"], 0) > now)
        if not group or group == self.spot_for:
            return False
        self.spot_for = group
        zone, fire = self.charge_zone(st), self.fire_tiles(st)

        budget = self.a.pull_budget if self.a.pull_budget > 0 else 1e9

        def cost(t):
            # more than the pull budget waking together first (aggro.View.overpull "at once"), then who would see
            # us before a pet, then how many would wake
            first, w = v.first_on_us(t, margin=0), v.wakes(t, margin=0)
            ww = [m for m in v.wakes(t) if len(v.wakes(t)) > 1]
            over = round(max(0.0, sum(m["weight"] for m in ww) - budget), 2)
            back = sum(aggro.weight(kind) for _, kind in v.backspawn_exposed(t))
            return (over, round(sum(m["weight"] for m in first) + back, 2), round(sum(m["weight"] for m in w), 2), len(w))
        here = cost(v.pos)
        best, best_c = None, None
        for t, k in self.tiles_within(v, PULL_STEPS).items():
            if k == 0 or t[:2] in zone or t[:2] in fire or v.never_near(t) \
                    or not any(m["serial"] in group for m in v.wakes(t, margin=0)):     # it must still see us there
                continue
            c = (*cost(t), k)
            if best_c is None or c < best_c:
                best, best_c = t, c
        if best is None or best_c[:3] >= (here[0], here[1], here[2] - 0.1):
            return False
        label = "to a pull spot"
        h.doing("hold", f"Stepping to {best[0]},{best[1]}: the pets between us and what comes", best[:2])
        self.count("pull_spots")
        log(f"pull spot {best[:2]}: {best_c[3]} would see us there, weight {best_c[1]:g} before a pet"
            + (f", {best_c[0]:g} over the pull budget together" if best_c[0] else "")
            + f" (here {here[3]}, {here[1]:g} before a pet" + (f", {here[0]:g} over" if here[0] else "") + ")")
        try:
            h.mover.walk_to(lambda: best[:2], 0, label, max_moves=2 * PULL_STEPS, max_route=2 * PULL_STEPS)
        except Abort as e:
            if not str(e).startswith(label):
                raise
            log(f"pull spot: {e}")
        return True

    @staticmethod
    def tiles_within(v: aggro.View, steps: int) -> dict:
        """{(x, y, z): walking steps} from where we stand, up to `steps` (pathfind.Walk.can_walk), never onto a known
        teleporter tile or one beside it (teleporters come in fields: live 2026-10-18 21:05:41 the step off the tank 3
        tiles from the known one at (5522, 637) went onto (5522, 636), unknown till then, and out to Maus 3)."""
        tele = [t for t in v.h.mover.teleporter_tiles(v.st["world"]["self"].get("map")) if cheb(t, v.pos) <= steps + 1]
        near = {(x + dx, y + dy) for x, y in tele for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
        out, frontier = {v.pos: 0}, [v.pos]
        for k in range(1, steps + 1):
            nxt = []
            for x, y, z in frontier:
                for d in range(8):
                    n = v.walk.can_walk(x, y, z, d)
                    if n is not None and n not in out and n[:2] not in near:
                        out[n] = k
                        nxt.append(n)
            frontier = nxt
        return out

    def evade(self, st) -> bool:
        """A --never-aggro monster in war mode near (aggro.View.chasing): 'All Follow Me' and run from it,
        to the gate when that is away from it, else EVADE_CLEAR_R beyond it, until it stops chasing (out
        of war mode or of view) (user 2026-10-13: "I run until the consort leashes and I'm in an area
        that I can manage"). The leave rules still send us to the gate when we're hurt."""
        h = self.h
        v = self.view(st)
        chase = v.chasing()
        if not chase:
            if self.evading:
                log("no longer chased: back to the hunt")
                self.evading, self.guard_needed = False, True
            return False
        m = min(chase, key=lambda c: cheb(c["pos"], v.pos))
        if not self.evading:
            self.evading = True
            self.count("evades")
            log(f"a {m['kind']} is coming ({cheb(m['pos'], v.pos)} tiles, war mode): running until it gives up")
            self.say("all follow me")
        label = f"away from the {m['kind']}"
        h.doing("evade", f"Running from a {m['kind']}", m["pos"][:2])

        def stop(s):
            return None if any(c["serial"] == m["serial"] for c in aggro.View(h, s).chasing()) else "it stopped chasing"
        gate = tuple(self.a.leave_gate) if getattr(self.a, "leave_gate", None) else None
        try:
            if gate is not None and cheb(gate, m["pos"]) > cheb(v.pos, m["pos"]) + 3:
                h.mover.walk_to(lambda: gate, 1, label, urgent=True, stop=stop, max_moves=EVADE_MOVES)
            else:
                h.mover.walk_to(None, 0, label, goal_fn=nav.beyond([m["pos"][:2]], EVADE_CLEAR_R), urgent=True,
                                stop=stop, max_moves=EVADE_MOVES)
        except Abort as e:
            if not str(e).startswith(label):
                raise
            log(f"evade: {e}")
        return True

    def out_of_fire(self, st) -> bool:
        """Standing on fire (FIRE_GRAPHICS): the nearest tile within FIRE_STEPS out of it and out of a
        Charge's reach, keeping the pets within --vet-range where one does; never mid-channel (a step
        breaks the channel's reach, and the fire hasn't been seen to hurt)."""
        h = self.h
        fire = self.fire_tiles(st)
        if not fire or time.monotonic() < self.vet_busy_until:
            return False
        v = self.view(st)
        if v.walk is None or v.pos[:2] not in fire:
            return False
        keep = [(p["x"], p["y"]) for p in self.pets(st) if not p["dead"] and p["dist"] <= self.a.vet_range]
        zone = self.charge_zone(st)
        ok = [(any(cheb(t, p) > self.a.vet_range for p in keep), k, t)
              for t, k in self.tiles_within(v, FIRE_STEPS).items()
              if k and t[:2] not in fire and t[:2] not in zone]
        if not ok:
            return False
        best = min(ok)[2]
        label = "out of the fire"
        h.doing("reposition", f"Stepping out of the fire to {best[0]},{best[1]}", best[:2])
        self.count("fire_steps")
        log(f"standing in fire at {v.pos[:2]}: stepping to {best[:2]}")
        try:
            h.mover.walk_to(lambda: best[:2], 0, label, max_moves=2 * FIRE_STEPS, max_route=2 * FIRE_STEPS)
        except Abort as e:
            if not str(e).startswith(label):
                raise
            log(f"out of the fire: {e}")
        return True

    def reposition(self, st) -> bool:
        """--reposition, in a fight: a tile within REPOSITION_STEPS that keeps the pets near us within
        --vet-range, out of a Charge's reach, with a lower aggro.View.stand_score (fewer idle monsters
        that would see us, fewer casters in war mode with sight of us) by REPOSITION_GAIN; never
        mid-channel, unless a caster in war mode sees us within its spell reach: then mid-channel too and
        up to CASTER_STEPS (sim fight1949: a second banshee cast at Logan for 20 s, -20 every 3.4 s, while the
        pets killed the first and the vet channels kept him from stepping out of its sight)."""
        h, now = self.h, time.monotonic()
        if not self.a.reposition or now - self.reposition_t < REPOSITION_S:
            return False
        v = self.view(st)
        casting = any(m["war"] and (m["caster"] and v.sees(m, v.pos, CASTER_R)
                                    or m["ranged"] and v.sees(m, v.pos, RANGED_R)) for m in v.mobs)
        if now < self.vet_busy_until and not casting:
            return False
        self.reposition_t = now
        if v.walk is None or not v.engaged():
            return False
        pos = v.pos
        keep = [(p["x"], p["y"]) for p in self.pets(st) if not p["dead"] and p["dist"] <= self.a.vet_range + 1]
        zone, fire = self.charge_zone(st), self.fire_tiles(st)
        spaced = [m["pos"][:2] for m in v.mobs if m["war"]] + keep if self.a.vet_range >= SPACING_R + 1 else []
        steps = self.tiles_within(v, CASTER_STEPS if casting else REPOSITION_STEPS)
        here = v.stand_score(pos)
        best, best_s = None, here
        for t, k in steps.items():
            if k == 0 or t[:2] in zone or t[:2] in fire or any(cheb(t, p) > self.a.vet_range for p in keep) \
                    or any(cheb(t, n) < SPACING_R for n in spaced):       # spacing's rule: not onto the tank
                continue
            s = v.stand_score(t) + 0.3 * k
            if s < best_s:
                best, best_s = t, s
        if best is None or here - best_s < REPOSITION_GAIN:
            return False
        label = "to a better tile"
        h.doing("reposition", f"Stepping to {best[0]},{best[1]}: fewer eyes on us ({here:.0f} -> {best_s:.0f})", best[:2])
        self.count("los_moves")
        try:
            n = 2 * (CASTER_STEPS if casting else REPOSITION_STEPS)
            h.mover.walk_to(lambda: best[:2], 0, label, urgent=casting, max_moves=n, max_route=n)
        except Abort as e:
            if not str(e).startswith(label):
                raise
            log(f"reposition: {e}")
        return True

    def area_zone(self, st) -> dict:
        """{(x, y): tiles to keep from it}: every monster in war mode in view whose hit lands on everyone
        near it (a charger: CHARGE_SAFE_R; AREA_R's), and each live pet beside one (the one it fights:
        a Charge lands next to it)."""
        import threats
        world = st["world"]
        labels = world.get("labels") or {}
        pets = [p for p in self.pets(st) if not p["dead"]]
        zone = {}
        for key, mob in world["mobiles"].items():
            s = serial_of(key)
            if s in self.roster or s in self.h.dead or mob.get("x") is None \
                    or not (mob.get("flags") or 0) & threats.FLAG_WARMODE:
                continue
            kind = type_key(labels.get(key) or mob.get("name"))
            r = CHARGE_SAFE_R if kind in self.chargers else AREA_R.get(kind)
            if r is None:
                continue
            c = (mob["x"], mob["y"])
            for t in [c] + [(p["x"], p["y"]) for p in pets if cheb((p["x"], p["y"]), c) <= PET_NEAR]:
                zone[t] = max(zone.get(t, 0), r)
        for tile, r in self.blink_tiles(st).items():
            zone[tile] = max(zone.get(tile, 0), r)
        return zone

    def blink_target(self, st, s: int) -> int | None:
        """Whom apparition `s` blinks onto: the defender of its latest swing, or the pet whose latest swing is at it
        (world.swings, within SWING_FRESH_S), else the nearest live pet or us within BLINK_TARGET_R of where it stood
        (in view or last seen), else None: unknown (live 20261017_110506 no 0x2F came from or at an apparition)."""
        world = st["world"]
        swings, now = world.get("swings") or {}, time.time()
        sw = swings.get(f"0x{s:08X}")
        if sw and now - (sw.get("t") or 0) <= SWING_FRESH_S and sw.get("defender") is not None:
            return serial_of(sw["defender"])
        for p in self.pets(st):
            w = swings.get(f"0x{p['serial']:08X}")
            if w and now - (w.get("t") or 0) <= SWING_FRESH_S and w.get("defender") is not None \
                    and serial_of(w["defender"]) == s:
                return p["serial"]
        mob = world["mobiles"].get(f"0x{s:08X}") or (world.get("last_seen") or {}).get(f"0x{s:08X}") or {}
        if mob.get("x") is None:
            return None
        c, me = (mob["x"], mob["y"]), self.h.me(st)
        near = [(cheb((p["x"], p["y"]), c), p["serial"]) for p in self.pets(st) if not p["dead"]]
        near.append((cheb(tuple(self.h.pos(st)[:2]), c), me))
        d, who = min(near)
        return who if d <= BLINK_TARGET_R else None

    def blink_tiles(self, st) -> dict:
        """{pet tile: AREA_R['apparition']} for each apparition that phased within BLINK_S onto a pet of ours: its hit
        lands there. One blinking onto us comes onto us wherever we stand, and one whose target we can't tell gives no
        tile: stepping off pets that follow us gets nowhere (simulator), and live 20261017_110506 the hits on Logan
        (7 of 40-48 in 20 blinks) came on an apparition's first blink from 6-16 tiles off the pets, onto him, or with
        him 1 from the tank it fought (11:10:32, -40): the case this covers."""
        now, out = time.monotonic(), {}
        pets = {p["serial"]: p for p in self.pets(st) if not p["dead"]}
        for t, target in self.blinks.values():
            p = pets.get(target)
            if p is not None and now - t <= BLINK_S:
                out[(p["x"], p["y"])] = AREA_R["apparition"]
        return out

    def charge_zone(self, st) -> set:
        """The tiles of area_zone: where an area hit lands; no tile to step to."""
        return set(self.area_zone(st))

    def heal_at(self, p: dict, zone: set) -> float:
        """The hits share below which pet `p` gets the vet supplies: --pet-heal-at, but only
        --standoff-vet-at for a pet a charger fights while our supplies don't reach CHARGE_SAFE_R
        (stepping in to heal it puts us in the Charge's reach: user 2026-10-13). --vet-range 3+ (the
        Healer's Codex 'Without Borders') heals it from outside."""
        if self.a.vet_range < CHARGE_SAFE_R and (p["x"], p["y"]) in zone:
            return min(self.a.pet_heal_at, self.a.standoff_vet_at)
        return self.a.pet_heal_at

    def stand_off(self, st) -> bool:
        """A charger or another area hitter in the fight (area_zone): keep its distance from it and the pet
        beside it, unless that pet needs healing (heal_at) and our supplies don't reach that far. Mid-channel
        too, and before a potion (HuntLoop.tick): live 20261015_171928 18:57:09-19.5 an apparition came onto
        Logan's tile and hit him -59; he stood there through a potion and a vet channel and its next hit
        took -60 more (8/100). A step doesn't stop a channel healing us (20261014_221341 22:35:50-55); it
        shortens the reach to a pet to 1 (wiki Veterinary). An apparition's "*phases*" onto a pet (blink_tiles):
        off that pet at once, past the repeat gap and the tank's heal, before the landing 1.1-1.6 s later."""
        h, now = self.h, time.monotonic()
        self.update(st)                   # a "*phases*" just heard: act on it in this tick, before a song or a channel
        pos = h.pos(st)
        landing = any(cheb(pos, t) < r for t, r in self.blink_tiles(st).items())
        if now - self.standoff_t < STANDOFF_REPEAT_S and not landing:
            return False
        zone = self.area_zone(st)
        if not zone or all(cheb(pos, t) >= r for t, r in zone.items()):
            return False
        if not landing and self.a.vet_range < max(zone.values()) \
                and any(p["frac"] < self.heal_at(p, set(zone)) or p["poisoned"] for p in self.pets(st)
                        if not p["dead"] and (p["x"], p["y"]) in zone) \
                and healing.in_pack(st["world"], h.me(st), VET_SUPPLIES_GRAPHIC):
            return False                                  # step in (or stay) to heal the tank
        to, stay = self.standoff_tile(st, zone)
        if stay:
            if stay != self.standoff_stay:
                log(f"stand-off: {stay}")
            self.standoff_stay = stay
            return False
        self.standoff_stay = None
        self.standoff_t = now
        label = "out of charge reach"
        h.doing("standoff", f"Standing off an area hitter ({len(zone)} tile(s) in its reach)")
        self.count("standoffs")
        try:
            if to is not None:
                h.mover.walk_to(lambda: to[:2], 0, label, urgent=True, max_moves=2 * STANDOFF_MOVES,
                                max_route=2 * STANDOFF_MOVES)
            else:
                h.mover.walk_to(None, 0, label, goal_fn=ClearOf(zone), urgent=True, max_moves=STANDOFF_MOVES,
                                max_route=STANDOFF_MOVES)
        except Abort as e:
            if not str(e).startswith(label):
                raise
            log(f"stand-off cut short: {e}")
        return True

    def standoff_tile(self, st, zone: dict):
        """Where to stand off to -> (tile, None), or (None, why) to stay: a tile within STANDOFF_MOVES steps
        clear of `zone` and fire that the fewest idle monsters would see before a pet (aggro.View.stand_score),
        the nearest of those, within --vet-range of every live pet near when one such is out of their sight,
        else beyond it. One that more idle monsters would see first than see us here is no stand-off: we stay
        (an area hit on us is better than a fresh monster on us). Live 20261015_171928 19:49:40 the nearest clear
        tile was 10 from a second apparition in its sight (it came onto Logan 5 s later, -46); 20261015_204514
        21:02:02 the only clear tiles in reach of the pets were 3 from an idle reanimated knight: it woke and
        hit Logan -46 (user: "Logan went and stood right in front of a reanimated knight while the pets were
        fighting another mob")."""
        v = self.view(st)
        if v.walk is None:
            return None, None
        fire, clear = self.fire_tiles(st), ClearOf(zone)
        keep = [(p["x"], p["y"]) for p in self.pets(st) if not p["dead"] and p["dist"] <= self.a.vet_range + 2]
        def closest(t):
            # the nearest idle monster that would see us there first: nearer is likelier to wake (live 21:02:02-07
            # the knight stayed idle 5 s at 6 tiles and woke 5 s after Logan stood 3 from it)
            return min((cheb(m["pos"], t) for m in v.first_on_us(t, margin=1)), default=99)
        near, far = None, None
        for t, k in self.tiles_within(v, STANDOFF_MOVES).items():
            if not clear(t) or t[:2] in fire:
                continue
            c = (round(v.stand_score(t)), -closest(t), k, t)
            if not any(cheb(t, p) > self.a.vet_range for p in keep):
                near = c if near is None or c < near else near
            far = c if far is None or c < far else far
        here = (round(v.stand_score(v.pos)), -closest(v.pos))
        pick = near if near is not None and near[0] == 0 else far if far is not None and far[0] == 0 else \
            min((c for c in (near, far) if c is not None), default=None)
        if pick is None:
            return None, None
        if pick[:2] > here:
            return None, (f"every clear tile is nearer an idle monster that would see us before a pet "
                          f"({pick[0] / 10:g} by weight at {-pick[1]} tiles; here {here[0] / 10:g} at {-here[1]}); staying")
        log(f"stand-off to {pick[3][:2]}: {pick[2]} step(s), seen by {pick[0] / 10:g} idle before a pet"
            + ("" if pick is near else ", beyond the vet supplies' reach of the pets"))
        return pick[3], None

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
        to = self.retreat_tile(st, tiles)
        log(f"retreating {RETREAT_R} tiles from {', '.join(str(a['name']) for a in near)} at "
            f"{me.get('hits')}/{me.get('hits_max')} hits; the pets hold it"
            + (f"; to {to[:2]}, out of sight of idle monsters where it can" if to else ""))
        self.count("retreats")
        try:
            if to is not None:
                h.mover.walk_to(lambda: to[:2], 0, label, urgent=True, max_moves=3 * RETREAT_R, max_route=3 * RETREAT_R)
            else:
                h.mover.walk_to(None, 0, label, goal_fn=nav.beyond(tiles, RETREAT_R - 1), urgent=True,
                                max_moves=2 * RETREAT_R)
        except Abort as e:
            if not str(e).startswith(label):
                raise
            log(f"retreat cut short: {e}")
        return True

    def retreat_tile(self, st, attackers: list):
        """Where to back off to: a tile RETREAT_R from every attacker within 2 x RETREAT_R steps that the
        fewest idle monsters would see (aggro.View.stand_score, a --never-aggro one above all), out of area
        hits and fire, the nearest of those; None without the map. Live 20261015_090607: backing off 7
        tiles west from an apparition put Logan 10 tiles from a second one, in its sight, and it came
        straight for him (-49) (user 2026-10-15: while the pets fight, a monster that sees us aggroes on us
        directly)."""
        v = self.view(st)
        if v.walk is None:
            return None
        zone, fire = self.charge_zone(st), self.fire_tiles(st)
        best, best_c = None, None
        for t, k in self.tiles_within(v, 2 * RETREAT_R).items():
            if min(cheb(t, a) for a in attackers) < RETREAT_R or t[:2] in zone or t[:2] in fire:
                continue
            c = (round(v.stand_score(t)), k)
            if best_c is None or c < best_c:
                best, best_c = t, c
        return best

    def vet_needed(self, st, pets, us_below: float | None = None) -> str | None:
        me = st["world"]["self"]
        zone = self.charge_zone(st)
        ghosts = [p for p in pets if p["dead"] and p["dist"] <= self.a.vet_range]
        if ghosts:
            return f"{ghosts[0]['name']} is dead (a channel resurrects it)"
        hurt = [p for p in pets if not p["dead"] and p["dist"] <= self.a.vet_range
                and (p["frac"] < self.heal_at(p, zone) or p["poisoned"])]
        if hurt:
            p = min(hurt, key=lambda p: p["frac"])
            return f"{p['name']} at {p['frac']:.0%}" + (" (poisoned)" if p["poisoned"] else "")
        if me.get("hits") is not None and self.h.frac(me) < (self.a.heal_at if us_below is None else us_below):
            # they heal us with no pet in reach: live 20261015_090607 09:16:36-41, the pets 4-5 tiles off at
            # --vet-range 2, "You finish using veterinary supplies." and +22 on Logan; the user 2026-10-15: "Logan
            # should have vet supply-bandaged himself when he got low even though he wasn't close enough to heal
            # the pets" (17:53, 100 -> 20 with no channel started)
            return f"us at {me.get('hits')}/{me.get('hits_max')}"
        return None

    def vet(self, st, us_below: float | None = None) -> bool:
        """Veterinary supplies when vet_needed and no channel runs: the stock double-click on the
        smallest stack (healing.in_pack: any bag depth, no bag opened), its answer read. `us_below`: our
        own share of hits that calls for it instead of --heal-at (on the run: 1.0, any damage)."""
        self.update(st)                   # a channel begun or finished since the last look (his own vet loop too)
        h, now = self.h, time.monotonic()
        if now < self.vet_busy_until:
            return False
        why = self.vet_needed(st, self.pets(st), us_below)
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
        h.human.wait("hotkey")
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
        elif reply == "busy":
            self.vet_busy_until = now + VET_BUSY_S        # a channel runs (its finish line frees it: update)
        else:
            self.vet_busy_until = now + USE_RETRY_S
        return True

    @staticmethod
    def _vet_reply(e) -> str | None:
        if e.get("ev") == "speech_heard":
            return {TEXT_VET_BEGIN: "begin", TEXT_VET_NEEDLESS: "needless", TEXT_VET_FAR: "far",
                    TEXT_ITEM_WAIT: "busy"}.get(e.get("text"))
        if e.get("ev") == "cliloc" and e.get("cliloc") == CLILOC_ACTION_WAIT:
            return "wait"
        return None

    def to_pet(self, st) -> bool:
        """A pet below --pet-heal-at beyond --vet-range: walk next to it (bandaging range is 1 after a
        step) when nothing is on us, no channel runs and supplies are there. --heal-in-place: a pet
        below --pet-leave-at is walked to even with something on us (healing it is the leave now)."""
        h, now = self.h, time.monotonic()
        if now < self.vet_busy_until or now - self.walk_pet_t < WALK_TO_PET_S \
                or not healing.in_pack(st["world"], h.me(st), VET_SUPPLIES_GRAPHIC):
            return False
        zone = self.charge_zone(st)
        busy = bool(h.attackers(st))
        far = [p for p in self.pets(st) if p["dist"] > self.a.vet_range and p["dist"] <= combat.VIEW_RANGE
               and (p["dead"] or ((p["frac"] < self.heal_at(p, zone) or p["poisoned"])
                                  and (not busy or (self.a.heal_in_place and p["frac"] < self.a.pet_leave_at))))]
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
            # to a live pet: stopping where an idle corpse purger would see us before the pets (shy_first). Sim: the walk
            # in beside the pets fighting a patrolled purger woke the second purger of the pair onto Logan (all 1213 of
            # its damage, the first patrol rule); pull_check's every rule here cost the zone crawl pet deaths (64 vs 48)
            def purger_first(s):
                v = self.view(s)
                return f"a {first[0]['kind']} would see us" if (first := self.shy_first(v, v.pos)) else None
            h.mover.walk_to(where, 1, label, max_moves=combat.VIEW_RANGE, stop=None if p["dead"] else purger_first)
        except Abort as e:
            if not str(e).startswith(label):
                raise
            log(f"walk to {p['name']}: {e}")
        return True

    def idle_pet(self, st, mob: dict, now: float) -> str | None:
        """The name of a live pet within PET_REACH of `mob` (the pets' fight) out of war mode for IDLE_PET_S,
        else None. Live 20261015_210709 21:19:51-20:43: AlaFlambe came out of a vampire countess' transfix
        and stood 45 s with war mode off while GraveRobber fought her alone (user: "AF was not fighting
        mobs, GR was fighting alone ... when it came back it didn't resume attacking"). [INFERENCE: a pet on
        guard fights what attacks us or itself, not what fights the other pet; the transfix cleared its target.]"""
        out = None
        for p in self.pets(st):
            s = p["serial"]
            if p["dead"] or p["war"] or cheb((p["x"], p["y"]), (mob["x"], mob["y"])) > PET_REACH:
                self.idle_since.pop(s, None)
                continue
            if now - self.idle_since.setdefault(s, now) >= IDLE_PET_S:
                out = p["name"]
        return out

    def rejoin(self, st) -> bool:
        """Back to within --vet-range - 1 of the nearest live pet, in view or where it was last seen alive (a
        fight 20 tiles off), unless a leave rule fires on the way: after a leave called off on the way out
        (HuntLoop.leave_called_off), or a monster on us with no pet near (fight). Live 20261015_171928
        19:49:55-50:16: called off at the gate with the pets out of view in the apparition fight, Logan stood
        there 20 s while a banshee 5 tiles off took him 100 -> 8."""
        h = self.h
        pos = h.pos(st)
        seen = {p["serial"]: p for p in self.pets(st)}
        spots = []
        for s, name in self.roster.items():
            p = seen.get(s)
            if p is not None:
                if not p["dead"]:
                    spots.append((p["dist"], s, name, (p["x"], p["y"])))
            elif s in self.seen_at and s not in self.dead_since:
                spots.append((cheb(pos, self.seen_at[s]), s, name, self.seen_at[s]))
        if not spots:
            return False
        dist, serial, name, at = min(spots)
        r = max(SPACING_R, self.a.vet_range - 1)
        if dist <= r:
            return False
        label = "back to the pets"
        h.doing("vet", f"Back to {name}", at)
        log(f"back to {name} at {at} ({dist} tiles{'' if serial in seen else ', last seen there'})")

        def where():
            m = h.link.last["world"]["mobiles"].get(f"0x{serial:08X}")
            return (m["x"], m["y"]) if m is not None and m.get("x") is not None else at
        try:
            h.mover.walk_to(where, r, label, max_moves=3 * combat.VIEW_RANGE, urgent=True,
                            stop=lambda s: (why := h.leave_reason(s)) and why[0])
        except Abort as e:
            if not str(e).startswith(label):
                raise
            log(f"back to the pets: {e}")
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
        if engaged and (idle := self.idle_pet(st, mob, now)) \
                and now - self.kill_t.get(serial, -1e9) >= KILL_REPEAT_S and self.effects.get(serial, {}).get("los", 0) < now:
            log(f"{idle} stands idle beside the fight with {name}: 'All Kill' at it")
            self.all_kill(serial)
            return
        if on_us and not engaged and not any(not p["dead"] and p["dist"] <= self.a.vet_range + 2 for p in self.pets(st)) \
                and self.rejoin(st):
            return                        # nothing between it and us here: to the pets (rejoin)
        if on_us and not engaged and now - self.attack_t[serial] >= KILL_AFTER_S \
                and now - self.kill_t.get(serial, -1e9) >= KILL_REPEAT_S \
                and self.effects.get(serial, {}).get("los", 0) < now:
            self.all_kill(serial)
            return
        if not on_us and serial not in self.pet_attackers(st) and (who := self.elsewhere(st, mob)) is not None \
                and not any(p["war"] and not p["dead"] and cheb((p["x"], p["y"]), (mob["x"], mob["y"])) <= PET_REACH
                            for p in self.pets(st)):
            # already fighting someone else and our pets on nothing near it: it won't come on; the pets go at it
            # (user 2026-10-17: "We need to just 'all kill' if the mob is already aggro'd on something else")
            if cheb(h.pos(st), (mob["x"], mob["y"])) <= BARD_RANGE and self.bard_sees(st, serial):
                if now - self.kill_t.get(serial, -1e9) >= KILL_REPEAT_S \
                        and self.effects.get(serial, {}).get("los", 0) < now:
                    log(f"{name} 0x{serial:08X} is fighting {who}: 'All Kill' at it")
                    self.all_kill(serial)
                    return
            elif cheb(h.pos(st), (mob["x"], mob["y"])) <= BARD_RANGE:
                if self.bard_step(st, serial):        # in range, out of our sight: a step that sees it
                    return
            elif not engaged:
                self.approach(serial, mob)
                return
        if not engaged:
            if not on_us and self.back_off(st):
                return                    # a purger would see us before the pets: back behind them first
            shy_idle = aggro.shy(type_key(mob.get("name"))) and not (mob.get("flags") or 0) & aggro.WAR
            if not on_us and not shy_idle and cheb(h.pos(st), (mob["x"], mob["y"])) > APPROACH_R \
                    and not self.hold_reason(st):
                self.approach(serial, mob)    # never toward an idle purger: it shoots from 12, it doesn't come on
                return
            if not on_us and shy_idle and self.patrol_pull(st, serial):
                return                    # the pets sent to wake it ("All Patrol"); patrol_watch calls them back
            if not on_us and self.unseen(st, serial):
                log(f"passing over {name} 0x{serial:08X}: it can't see us from "
                    f"{cheb(h.pos(st), (mob['x'], mob['y']))} tiles; skipping it for {SKIP_S:.0f} s")
                h.skip[serial] = now + SKIP_S
                h.disengage("dropped")
                return
            if not on_us and self.hold_reason(st) and self.pull_spot(st):
                return                    # where it comes to the pets, out of the others' sight (sim fight1949)
            h.doing("wait", f"Pets on guard; waiting for {name} to come on", (mob["x"], mob["y"]), serial)
            time.sleep(0.4)
            return
        if self.bard(st, serial, mob):
            return
        h.doing("fight", f"The pets are fighting {name}", (mob["x"], mob["y"]), serial)
        time.sleep(0.4)

    def unseen(self, st, serial: int) -> bool:
        """`serial` is idle and wouldn't see us here within APPROACH_R, a Wall-flagged static blocking too
        (aggro.View.sees strict, as the hold): fight passes it over. Live 20261017_181210: three targets dropped
        after 15 s of "waiting for ... to come on" 6 tiles off, idle, out of sight even by the server's rule (a
        shade wolf 18:17:02, a vampire bat 18:19:44, a tormented soul 18:24:48; user: "waiting for <mob> to come
        on when the mob can't see Logan through walls"). Walking on into its sight cost the simulator more
        deaths (docs/NOTES.md "Targets behind walls")."""
        v = self.view(st)
        m = next((m for m in v.mobs if m["serial"] == serial), None)
        return m is not None and not m["war"] and not v.sees(m, v.pos, APPROACH_R, strict=True)

    def approach(self, serial: int, mob: dict):
        """Walk toward a target the pets haven't engaged until APPROACH_R (its aggro range): the
        pets on guard engage it when it comes on. Stops as soon as it is hurt or attacks us."""
        h, label = self.h, "toward a target"
        h.doing("approach", f"Approaching {mob.get('name') or hex(serial)}", (mob["x"], mob["y"]), serial)

        def where():
            m = h.link.last["world"]["mobiles"].get(f"0x{serial:08X}") or mob
            return m["x"], m["y"]

        def stop(st):
            return self.engaged(st, serial) or serial in {serial_of(a["serial"]) for a in h.attackers(st)} \
                or self.pull_check(st)
        try:
            h.mover.walk_to(where, APPROACH_R, label, max_moves=APPROACH_MOVES, stop=stop)
        except Abort as e:
            if not str(e).startswith(label):
                raise
            log(f"approach: {e}")

    def bard(self, st, serial: int, mob: dict) -> bool:
        """The next bard skill at a target the pets fight: discord, provoke another, peace. True when
        one went out (or we stepped to see the target: bard_step). Only at a target we see by the server's rule
        (bard_sees), and a provoked pair both in sight (user 2026-10-17: "A lot of times it tries to bard a mob
        that cannot be seen and fails": 44 of 418 bard uses today answered "out of sight or reach")."""
        now = time.monotonic()
        if now < self.skill_ready or self.no_instrument:
            return False
        fx = self.effects.setdefault(serial, {})
        if fx.get("los", 0) > now or cheb(self.h.pos(st), (mob["x"], mob["y"])) > BARD_RANGE:
            return False
        kind = type_key(mob.get("name"))
        if aggro.shy(kind) and serial not in {serial_of(a["serial"]) for a in self.h.attackers(st)} \
                and not self.engaged(st, serial):
            # our skill draws its aggro before the pets hit it (user 2026-10-12: an aggressive act of ours puts the mob on
            # us) and a purger then barrages us wherever the pets stand; on us, or on the pets, the skills go (live demo
            # 2026-10-18 17:56:02: his Discordance on the purger GraveRobber fought, never a hit on Logan)
            return False
        due = (self.a.discord and fx.get("discord", 0) <= now and (SKILL_DISCORD, kind) not in self.beyond) or (
            now >= self.calm_ready and (self.a.provoke or self.a.peace) and fx.get("calm", 0) <= now
            and fx.get("immune", 0) <= now)
        if due and not self.bard_sees(st, serial):
            return self.bard_step(st, serial)
        if self.a.discord and fx.get("discord", 0) <= now and (SKILL_DISCORD, kind) not in self.beyond:
            return self.use(SKILL_DISCORD, serial)
        if now < self.calm_ready:
            return False
        if self.a.provoke:
            others = [s for s in self.others(st, serial)
                      if self.effects.get(s, {}).get("calm", 0) <= now and self.effects.get(s, {}).get("immune", 0) <= now
                      and self.effects.get(s, {}).get("los", 0) <= now
                      and (SKILL_PROVO, type_key((st["world"]["mobiles"].get(f"0x{s:08X}") or {}).get("name")))
                      not in self.beyond and self.bard_sees(st, s)
                      # a purger the pets haven't hurt: our skill draws its barrage (sim: "Provocation on a corpse
                      # purger onto" the patrolled one put the second of the pair on Logan)
                      and not (aggro.shy(type_key((st["world"]["mobiles"].get(f"0x{s:08X}") or {}).get("name")))
                               and not self.engaged(st, s))]
            if len(others) > 1 or (others and self.bard_sees(st, serial)):
                return self.use(SKILL_PROVO, others[0], others[1] if len(others) > 1 else serial)
        if self.a.peace and fx.get("calm", 0) <= now and fx.get("immune", 0) <= now \
                and (SKILL_PEACE, kind) not in self.beyond:
            return self.use(SKILL_PEACE, serial)
        return False

    def bard_sees(self, st, serial: int, tile=None) -> bool:
        """We'd see `serial` from `tile` (where we stand) within BARD_RANGE by the server's rule (aggro.View.we_see;
        replayed live answers 2026-10-17: the 17 "Target cannot be seen." out of sight, 78 of 79 answered ones in
        sight). True when it isn't in the view model or there's no map."""
        v = self.view(st)
        m = next((m for m in v.mobs if m["serial"] == serial), None)
        return m is None or v.we_see(m, tile or v.pos, BARD_RANGE)

    def bard_step(self, st, serial: int) -> bool:
        """A bard skill is due at `serial` and we don't see it: step (up to REPOSITION_STEPS, at most once every
        REPOSITION_S) to the tile that sees it with the fewest eyes on us (aggro.View.stand_score), under the
        reposition's rules (the pets within --vet-range, out of a Charge's reach and fire, not onto the tank),
        and no more eyes on us than here (stand_score plus 0.3 a step: allowing REPOSITION_GAIN more cost the
        simulator 9 runs with a death in 144, docs/NOTES.md "Bard skills at targets out of sight"). Never
        mid-channel. True when we stepped."""
        h, now = self.h, time.monotonic()
        if now - self.bard_step_t < REPOSITION_S or now < self.vet_busy_until:
            return False
        self.bard_step_t = now
        v = self.view(st)
        m = next((m for m in v.mobs if m["serial"] == serial), None)
        if v.walk is None or m is None:
            return False
        keep = [(p["x"], p["y"]) for p in self.pets(st) if not p["dead"] and p["dist"] <= self.a.vet_range + 1]
        zone, fire = self.charge_zone(st), self.fire_tiles(st)
        spaced = [o["pos"][:2] for o in v.mobs if o["war"]] + keep if self.a.vet_range >= SPACING_R + 1 else []
        here = v.stand_score(v.pos)
        best, best_s = None, None
        for t, k in self.tiles_within(v, REPOSITION_STEPS).items():
            if k == 0 or t[:2] in zone or t[:2] in fire or any(cheb(t, p) > self.a.vet_range for p in keep) \
                    or any(cheb(t, n) < SPACING_R for n in spaced) or not v.we_see(m, t, BARD_RANGE):
                continue
            s = v.stand_score(t) + 0.3 * k
            if best_s is None or s < best_s:
                best, best_s = t, s
        if best is None or best_s > here:
            return False
        label = "to see the bard target"
        h.doing("reposition", f"Stepping to {best[0]},{best[1]} to see {m['kind']}", best[:2])
        self.count("bard_los_moves")
        log(f"{m['kind']} 0x{serial:08X} out of our sight: stepping to {best[:2]}")
        try:
            h.mover.walk_to(lambda: best[:2], 0, label, max_moves=2 * REPOSITION_STEPS, max_route=2 * REPOSITION_STEPS)
        except Abort as e:
            if not str(e).startswith(label):
                raise
            log(f"bard step: {e}")
        return True

    def _instrument(self, mark: int, cur):
        """When `cur` is the server's instrument prompt (CLILOC_PICK_INSTRUMENT: the one last played is gone;
        live 20261015_171928 after the 18:40 death 54 prompts, each answered with the skill's target, "That
        is not a musical instrument.", every bard skill and song lost): one from the pack (INSTRUMENT_GRAPHICS),
        then the skill's own cursor. -> the cursor to answer, or None (none in the pack: cancelled and the bard
        skills stopped, a juncture posted)."""
        h = self.h
        if not any(e.get("ev") == "cliloc" and e.get("cliloc") == CLILOC_PICK_INSTRUMENT for e in h.link.events[mark:]):
            return cur
        st = h.state()
        found = next((kv for g in INSTRUMENT_GRAPHICS for kv in healing.in_pack(st["world"], h.me(st), g)), None)
        if found is None:
            h.cancel(cur)
            self.no_instrument = True
            h.memory.juncture("hunt", "low_supplies", "No instrument in the pack: no bard skills or songs", "attention",
                              {"item": "instrument"})
            log("no instrument in the pack: the bard skills and songs are off")
            return None
        serial, it = found
        h.human.wait("hotkey_aim")
        mark2 = len(h.link.events)
        h.link.act(actions.target_object(cur["cursor_id"], serial, it.get("x") or 0, it.get("y") or 0, it.get("z") or 0,
                                         it.get("graphic") or 0, cur.get("cursor_type") or 0))
        log(f"instrument: {it.get('name') or 'graphic 0x%04X' % (it.get('graphic') or 0)} 0x{serial:08X}")
        return self._cursor(mark2)

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

    def _aim(self, cur, serial: int, rng: int, bard: bool = False) -> bool:
        """Answer the cursor `cur` with `serial` after a hotkey-paced aim (humanize "hotkey_aim") and a fresh
        read: the target still live (the monsters-only guard) and no leave rule firing, and for a bard skill no
        potion to drink first (potion_first); else the stock Esc."""
        h = self.h
        h.human.wait("hotkey_aim")
        st = h.state()
        if not h.cursor_still(st, cur):
            log("the cursor went away before the target answer")
            return False
        mob, why = h.live_target(st, serial, rng)
        if mob is None or h.leave_reason(st) or (bard and self.potion_first(st)):
            log(f"not answering the cursor with 0x{serial:08X}: {why or 'a leave rule fired or a potion is due'}; "
                f"cancelling")
            h.cancel(cur)
            return False
        h.link.act(combat.target_mobile(cur, serial, mob))
        return True

    def potion_first(self, st) -> bool:
        """Below --heal-at with a heal potion ready now: drink before a bard skill goes out (live 20261015_171928
        19:19:30.9-35.2: a Discordance started at 100 hits, -58 meanwhile, its cursor answered at 42, the
        potion 3.6 s later)."""
        me = st["world"]["self"]
        if me.get("hits") is None or self.h.frac(me) >= self.a.heal_at:
            return False
        due = (self.h.potions.last or -1e9) + healing.POTION_COOLDOWN_S - time.monotonic()
        return due <= 0 and bool(healing.heal_potions(st["world"], self.h.me(st)))

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
        h.human.wait("hotkey")
        mark = len(h.link.events)
        h.link.act(actions.use_skill(sid))
        now = time.monotonic()
        self.skill_ready = now + SKILL_GAP_S
        self.count("bard")
        cur = self._cursor(mark)
        if cur is not None and (cur := self._instrument(mark, cur)) is None:
            return True
        if cur is None:
            refused = [bard_answer(e) for e in h.link.events[mark:] if bard_answer(e)]
            if any(r[1] == "calm_wait" for r in refused):
                self.calm_ready = time.monotonic() + SKILL_GAP_S
            else:
                self.skill_ready = time.monotonic() + SKILL_RETRY_S
            log(f"{what}: no target cursor ({[r[1] for r in refused] or 'no answer'})")
            return True
        if not self._aim(cur, serial, BARD_RANGE, bard=True):
            return True
        if second is not None:
            mark2 = len(h.link.events)
            cur2 = self._cursor(mark2, not_id=None)
            if cur2 is None or not self._aim(cur2, second, BARD_RANGE, bard=True):
                log(f"{what}: no second target")
                return True
        ans = mark
        h.wait_for(lambda s: any(bard_answer(e) for e in h.link.events[ans:]
                                 if not (e.get("ev") == "speech_heard" and e.get("text") == TEXT_INCITE_SECOND)),
                   ANSWER_WAIT_S)
        self.answered(sid, serial, second, name, [bard_answer(e) for e in h.link.events[ans:] if bard_answer(e)])
        h.human.wait("hotkey")
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

    def jam(self, st, walking_out: bool = False) -> str | None:
        """Why we're in a jam, else None: two or more monsters attacking us within JAM_R, one when we're below
        --retreat-at, or (walking out to the gate) any monster in war mode within JAM_R of us."""
        me = st["world"]["self"]
        near = [a for a in self.h.attackers(st) if a["dist"] <= JAM_R]
        if len(near) >= 2:
            return f"{len(near)} on us within {JAM_R}"
        if near and me.get("hits") is not None and self.h.frac(me) < self.a.retreat_at:
            return f"{near[0]['name'] or 'one'} on us at {me['hits']}/{me.get('hits_max')} hits"
        if walking_out:
            v = self.view(st)
            war = [m for m in v.mobs if m["war"] and cheb(m["pos"], v.pos) <= JAM_R]
            if war:
                return f"walking out past {len(war)} in war mode within {JAM_R}"
        return None

    def area_peace(self, st, walking_out: bool = False) -> bool:
        """--peace, in a jam (Team.jam): Peacemaking with its cursor answered with ourselves, a 2 s pacify on each
        creature within AREA_PEACE_R that passes its check (TEXT_AREA_PEACE_OK). True when the skill went out."""
        now = time.monotonic()
        if not self.a.peace or self.no_instrument or now < self.skill_ready or now < self.calm_ready:
            return False
        why = self.jam(st, walking_out)
        if why is None:
            return False
        h = self.h
        h.doing("bard", f"Peacemaking around us: {why}")
        h.human.wait("hotkey")
        mark = len(h.link.events)
        h.link.act(actions.use_skill(SKILL_PEACE))
        self.count("area_peace")
        self.skill_ready = time.monotonic() + SKILL_RETRY_S
        cur = self._cursor(mark)
        if cur is not None and (cur := self._instrument(mark, cur)) is None:
            return True
        if cur is None:
            log(f"area peace ({why}): no target cursor")
            return True
        h.human.wait("hotkey_aim")
        st = h.state()
        if not h.cursor_still(st, cur):
            return True
        me = st["world"]["self"]
        mark2 = len(h.link.events)
        h.link.act(combat.target_self(cur, h.me(st), h.pos(st)[:3], me.get("body") or me.get("graphic")))
        h.wait_for(lambda s: any(e.get("ev") == "speech_heard" and (e.get("text") == TEXT_AREA_PEACE_OK or bard_answer(e))
                                 for e in h.link.events[mark2:]), ANSWER_WAIT_S)
        now = time.monotonic()
        ok = any(e.get("ev") == "speech_heard" and e.get("text") == TEXT_AREA_PEACE_OK for e in h.link.events[mark2:])
        self.skill_ready = now + SKILL_GAP_S
        self.calm_ready = now + (CALM_SUCCESS_S if ok else SKILL_GAP_S)
        log(f"area peace ({why}): " + ("some pacified for 2 s" if ok else "none pacified"))
        return True

    def song(self, st) -> bool:
        """--songs: the next barding song due (SONG_ORDER; none running or ending within SONG_RENEW_S, by
        song_left), the skill at a ground tile beside us. True when the skill went out."""
        now = time.monotonic()
        if not getattr(self.a, "songs", False) or self.no_instrument or now < self.song_ready or now < self.skill_ready:
            return False
        sid = next((s for s in SONG_ORDER if self.song_left(st, s, now) < SONG_RENEW_S), None)
        if sid is None:
            return False
        h = self.h
        what = f"Song of {SONG_NAMES[sid]}"
        h.doing("bard", what)
        h.human.wait("hotkey")
        mark = len(h.link.events)
        h.link.act(actions.use_skill(sid))
        self.count("song")
        self.song_ready = time.monotonic() + SONG_GAP_S          # whatever the answer: no retry storm
        self.skill_ready = time.monotonic() + SKILL_RETRY_S
        cur = self._cursor(mark)
        if cur is not None and (cur := self._instrument(mark, cur)) is None:
            return True
        if cur is None:
            log(f"{what}: no target cursor")
            return True
        h.human.wait("hotkey_aim")
        st = h.state()
        if not h.cursor_still(st, cur):
            log(f"{what}: the cursor went away")
            return True
        if h.leave_reason(st) or self.potion_first(st):
            h.cancel(cur)
            return True
        x, y, z = h.pos(st)[:3]
        walk = h.mover.walk_map(st)
        beside = [n for n in (walk.can_walk(x, y, z, d) for d in range(8)) if n] if walk is not None else []
        gx, gy, gz = beside[0] if beside else (x, y, z)        # live: a tile 1-3 away, never our own
        h.link.act(actions.target_xyz(cur["cursor_id"], gx, gy, gz, 0, cur.get("cursor_type") or 0))
        h.wait_for(lambda s: any(song_answer(e) for e in h.link.events[mark:]), ANSWER_WAIT_S)
        got = next((a for a in (song_answer(e) for e in h.link.events[mark:]) if a), None)
        if got is None:
            log(f"{what}: no answer")
        elif got[0] == "on":
            self.songs[got[1]] = time.monotonic() + SONG_S
            self.count("song_on")
            log(f"{what}: playing for {SONG_S / 60:.0f} min")
        else:
            log(f"{what}: {got[0]}")
        h.human.wait("hotkey")
        return True

    def song_left(self, st, sid: int, now: float) -> float:
        """Seconds the song still runs: the later of what we played (self.songs) and its buff on us
        (SONG_BUFF_ICONS, by the end the server gave it; one without an end counts as just begun). Live
        20261015_115604 the runner replayed all three songs on arrival though they ran from before it
        started (Discordance 702 s left)."""
        left = self.songs.get(sid, 0.0) - now
        world = st["world"]
        buffs = (world.get("buffs") or {}).get(f"0x{self.h.me(st):08X}") or {}
        b = buffs.get(str(SONG_BUFF_ICONS[sid])) or buffs.get(SONG_BUFF_ICONS[sid])
        if b is not None and not b.get("expired"):
            ends = b.get("ends_t")
            left = max(left, SONG_S if ends is None else ends - time.time())
        return left

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

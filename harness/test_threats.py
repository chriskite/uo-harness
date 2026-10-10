"""Tests for harness/threats.py on synthetic state-port responses (no network).

Run: python harness/test_threats.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from threats import (CREATURE_SPELL_RANGE, Params, Watch, assess, flee_radius, friendly, hit_attackers,  # noqa: E402
                     spell_on_us, unseen_attackers)

FAILURES = []
ME = 0x00094375
NOW = 1000.0
HERE = (1000, 1000)


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def eq(name, got, want):
    check(name, got == want, f"(got {got!r}, want {want!r})")


def mob(serial, dx, dy, *, body=0x190, noto=1, flags=0x20, pet=None):
    m = {"graphic": body, "notoriety": noto, "flags": flags, "x": HERE[0] + dx, "y": HERE[1] + dy, "z": 0}
    if pet:
        m["pet"] = pet
    return (f"0x{serial:08X}", m)


def state(mobs=(), *, labels=None, items=None, hits=50, body=0x190, pos=HERE, events=(), swings=None):
    st = {
        "movement": {"pos": [*pos, 0, 0] if pos else None, "self_serial": ME},
        "world": {
            "self": {"serial": f"0x{ME:08X}", "hits": hits, "hits_max": 50,
                     "stats": {"graphic": body}},
            "mobiles": dict(mobs),
            "items": items or {},
            "labels": {f"0x{s:08X}": t for s, t in (labels or {}).items()},
        },
        "events": [{"seq": i, "t": t, "origin": "world", "data": d}
                   for i, (t, d) in enumerate(events)],
    }
    if swings is not None:      # world.swings: {attacker_hex: {defender: hex, t}}
        st["world"]["swings"] = {f"0x{a:08X}": {"defender": f"0x{d:08X}", "t": t}
                                 for a, (d, t) in swings.items()}
    return st


def one(a, serial):
    return next(t for t in a.threats if t.serial == serial)


def test_reds():
    print("== reds: flee inside the flee radius, watch outside ==")
    eq("flee radius on foot (12 + floor(5 / 0.2))", flee_radius(0.2, 12, 4.0, 1.0), 37)
    eq("flee radius mounted", flee_radius(0.1, 12, 1.0, 0.5), 27)
    # recall 1.0 + margin 0.5 -> on-foot radius 12 + 7 = 19
    w = Watch()
    far = state([mob(0x100, 25, 3, noto=6)], labels={0x100: "Killer"})
    a = w.update(far, recall_s=1.0, margin_s=0.5, now=NOW)
    t = one(a, 0x100)
    eq("far red kind", t.kind, "red")
    eq("far red distance (Chebyshev)", t.distance, 25)
    eq("far red eta (25 - 12) * 0.2", t.eta_s, 2.6)
    eq("far red -> watch", t.action, "watch")
    eq("far red overall watch", a.action, "watch")
    near = state([mob(0x100, 18, -18, noto=6)], labels={0x100: "Killer"})
    a = w.update(near, recall_s=1.0, margin_s=0.5, now=NOW + 1)
    eq("approaching red -> flee", one(a, 0x100).action, "flee")
    eq("approaching red overall flee", a.action, "flee")
    check("flee reason names the red", any("red 0x00000100" in r for r in a.reasons), a.reasons)
    eq("flee list", [t.serial for t in a.flee], [0x100])
    # same far red, mounted (item on layer 0x19): 0.1 s/tile -> eta 1.3 <= 1.5
    mount = {"0x40000001": {"graphic": 0x3E9F, "layer": 0x19, "container": "0x00000100"}}
    a = assess(state([mob(0x100, 25, 3, noto=6)], items=mount),
               recall_s=1.0, margin_s=0.5, now=NOW)
    t = one(a, 0x100)
    eq("mounted red detected", t.mounted, True)
    eq("mounted red eta", t.eta_s, 1.3)
    eq("mounted far red -> flee", t.action, "flee")
    # a grey (criminal) player close by flees too; an orange (enemy) as well
    a = assess(state([mob(0x101, 5, 0, noto=4), mob(0x102, 0, 6, noto=5)]),
               recall_s=1.0, margin_s=0.5, now=NOW)
    eq("grey player kind/action", (one(a, 0x101).kind, one(a, 0x101).action), ("grey", "flee"))
    eq("orange player kind/action", (one(a, 0x102).kind, one(a, 0x102).action), ("orange", "flee"))
    # a polymorphed red player (non-human body, the player flag set) is still red
    a = assess(state([mob(0x103, 4, 4, body=0xD9, noto=6, flags=0x20)]),
               recall_s=1.0, margin_s=0.5, now=NOW)
    eq("polymorphed player -> red player", (one(a, 0x103).kind, one(a, 0x103).player),
       ("red", True))
    # stale entry far beyond any update range is ignored
    a = assess(state([mob(0x104, 200, 0, noto=6)]), recall_s=4.0, margin_s=1.0, now=NOW)
    eq("stale far red -> ignore", one(a, 0x104).action, "ignore")
    # own position unknown: a hostile is watched, not fled from blindly
    a = assess(state([mob(0x105, 1, 1, noto=6)], pos=None), recall_s=4.0, margin_s=1.0, now=NOW)
    eq("no own position -> watch", one(a, 0x105).action, "watch")


def test_npcs_and_players():
    print("== npcs, blue players, ghosts ==")
    mobs = [mob(0x200, 2, 0, noto=7, flags=0),                 # Len the banker
            mob(0x201, 3, 0, body=0x191, noto=3, flags=0x02),  # gray battle trainer
            mob(0x202, 4, 0, noto=1, flags=0x20),              # young player
            mob(0x203, 30, 0, noto=1, flags=0x20),             # blue beyond watch radius
            mob(0x204, 1, 0, body=0x192, noto=6, flags=0),     # red ghost
            mob(0x205, 5, 5, noto=1, flags=0)]                 # human, no evidence
    labels = {0x200: "Len the banker", 0x201: "Riane the battle trainer",
              0x202: "Bresh Fiscuits (Young)", 0x203: "Vorn"}
    a = assess(state(mobs, labels=labels), recall_s=4.0, margin_s=1.0, now=NOW)
    eq("banker kind/action", (one(a, 0x200).kind, one(a, 0x200).action), ("npc", "ignore"))
    eq("banker named", one(a, 0x200).name, "Len the banker")
    eq("gray titled trainer -> npc ignore", (one(a, 0x201).kind, one(a, 0x201).action),
       ("npc", "ignore"))
    eq("young player -> blue watch", (one(a, 0x202).kind, one(a, 0x202).player,
                                      one(a, 0x202).action), ("blue", True, "watch"))
    eq("blue beyond watch radius -> ignore", one(a, 0x203).action, "ignore")
    eq("ghost -> ignore", (one(a, 0x204).kind, one(a, 0x204).action), ("ghost", "ignore"))
    eq("untitled human -> assumed player", one(a, 0x205).player, True)
    eq("overall watch (nothing hostile)", a.action, "watch")
    eq("flee first ordering: watch before ignore",
       [t.action for t in a.threats], sorted([t.action for t in a.threats],
                                             key=("flee", "watch", "ignore").index))
    a = assess(state([mob(0x200, 2, 0, noto=7, flags=0)], labels={0x200: "Len the banker"}),
               recall_s=4.0, margin_s=1.0, now=NOW)
    eq("banker alone -> overall ignore", a.action, "ignore")
    a = assess(state(body=0x192), recall_s=4.0, margin_s=1.0, now=NOW)
    eq("self ghost -> dead", a.dead, True)
    # live 2026-10-03, Urukton Bluffs: human-bodied spawn with creature-style names, no player flag
    urk = [mob(0x210, 6, 0, noto=3, flags=0),                 # an orc hunter (body 400)
           mob(0x211, 7, 0, noto=1, flags=0),                 # a prevalian footman (NPC soldier)
           mob(0x212, 8, 0, noto=3, flags=0x20)]              # a player who happens to be named "an ..."
    a = assess(state(urk, labels={0x210: "an orc hunter", 0x211: "a prevalian footman", 0x212: "an orc"}),
               recall_s=4.0, margin_s=1.0, now=NOW)
    eq("human body named 'an orc hunter', grey, no player flag -> a monster, not a grey player",
       (one(a, 0x210).kind, one(a, 0x210).player), ("monster", None))
    eq("human body named 'a prevalian footman', innocent -> an NPC", (one(a, 0x211).kind, one(a, 0x211).player),
       ("npc", False))
    eq("the player flag still wins over a creature-like name", (one(a, 0x212).kind, one(a, 0x212).player),
       ("grey", True))
    a = assess(state([mob(0x213, 6, 0, noto=6, flags=0)], labels={0x213: "a PK"}),
               recall_s=4.0, margin_s=1.0, now=NOW)
    eq("a red human named like a creature stays a player (never attackable by mistake)",
       (one(a, 0x213).kind, one(a, 0x213).player), ("red", True))
    # live 2026-10-05 (witcher_268): "a norse bear rider" (body 400, flags 0) went from notoriety 3 to 4 as it
    # attacked; read as a grey player the runner recalled on the spot (disturbed) instead of running
    w = Watch()
    labels = {0x214: "a norse bear rider"}
    w.update(state([mob(0x214, 12, 0, noto=3, flags=0)], labels=labels), recall_s=4.0, margin_s=1.0, now=NOW)
    a = w.update(state([mob(0x214, 5, 0, noto=4, flags=0x40)], labels=labels), recall_s=4.0, margin_s=1.0,
                 now=NOW + 1)
    eq("a spawned creature seen as a monster stays one when it turns grey (4) to attack",
       (one(a, 0x214).kind, one(a, 0x214).player, one(a, 0x214).hostile), ("monster", None, True))
    a = assess(state([mob(0x215, 5, 0, noto=4, flags=0), mob(0x216, 6, 0, noto=4, flags=0),
                      mob(0x217, 7, 0, noto=3, flags=0), mob(0x218, 8, 0, noto=4, flags=0x20),
                      mob(0x219, 9, 0, noto=3, flags=0)],
                     labels={0x215: "a norse bear rider", 0x216: "a rime spirit soldier", 0x217: "ghostly footman",
                             0x218: "a rime spirit soldier", 0x219: "Roy Rina"}),
               recall_s=4.0, margin_s=1.0, now=NOW)
    eq("first seen already grey (4) with a creature label and no player flag: a monster (live 2026-10-08: "
       "'a rime spirit soldier' sent a trip home as a grey player)",
       [(one(a, s).kind, one(a, s).player) for s in (0x215, 0x216)], [("monster", None)] * 2)
    eq("a lowercase name with no article ('ghostly footman', grey, no flag): a monster",
       (one(a, 0x217).kind, one(a, 0x217).player), ("monster", None))
    eq("the player flag still makes a criminal named like a creature a player",
       (one(a, 0x218).kind, one(a, 0x218).player), ("grey", True))
    eq("a capitalised name without the flag is still assumed a player",
       (one(a, 0x219).kind, one(a, 0x219).player), ("grey", True))


def test_monsters():
    print("== monsters by aggression params ==")
    ape = mob(0x300, 3, 0, body=0x1D, noto=1, flags=0)        # captured: blood ape, noto 1
    sheep = mob(0x301, 2, 0, body=0xCF, noto=3, flags=0)
    angry_sheep = mob(0x302, 2, 1, body=0xCF, noto=3, flags=0x40)
    far_ape = mob(0x303, 20, 0, body=0x1D, noto=1, flags=0)
    labels = {0x300: "a blood ape", 0x301: "a sheep", 0x302: "a sheep", 0x303: "a blood ape"}
    st = state([ape, sheep, angry_sheep, far_ape], labels=labels)
    aggressive = Params(monster_default_aggressive=True)
    a = assess(st, recall_s=4.0, margin_s=1.0, now=NOW)
    eq("unknown creature, not in war mode -> not aggressive by default -> ignore",
       (one(a, 0x300).kind, one(a, 0x300).aggressive, one(a, 0x300).action), ("monster", False, "ignore"))
    eq("passive body (sheep) -> ignore", one(a, 0x301).action, "ignore")
    eq("sheep in war mode, not swinging at us (someone is killing it) -> ignore",
       (one(a, 0x302).aggressive, one(a, 0x302).action), (False, "ignore"))
    a = assess(state([angry_sheep], labels=labels, swings={0x302: (ME, NOW - 1.0)}),
               recall_s=4.0, margin_s=1.0, now=NOW)
    eq("sheep in war mode swinging at us -> flee", one(a, 0x302).action, "flee")
    goat = mob(0x304, 2, 0, body=0xD1, noto=3, flags=0)
    walrus = mob(0x305, 2, 0, body=0xDD, noto=3, flags=0)
    a = assess(state([goat, walrus]), recall_s=4.0, margin_s=1.0, now=NOW, params=aggressive)
    eq("goat and walrus are passive even when unknown creatures count as dangerous (live aborts)",
       (one(a, 0x304).action, one(a, 0x305).action), ("ignore", "ignore"))
    a = assess(st, recall_s=4.0, margin_s=1.0, now=NOW, params=aggressive)
    eq("monster_default_aggressive=True: unknown creature -> flee",
       (one(a, 0x300).aggressive, one(a, 0x300).action), (True, "flee"))
    eq("monster eta (3 - 1) * 0.4", one(a, 0x300).eta_s, 0.8)
    eq("far aggressive monster -> watch (eta 7.6 > 5)", (one(a, 0x303).eta_s,
                                                         one(a, 0x303).action), (7.6, "watch"))
    calm = Params(monster_default_aggressive=True, passive_bodies=frozenset({0x1D, 0xCF}))
    a = assess(st, recall_s=4.0, margin_s=1.0, now=NOW, params=calm)
    eq("passive_bodies param -> ignore", one(a, 0x300).action, "ignore")
    eq("war mode doesn't beat passive_bodies", one(a, 0x302).action, "ignore")
    a = assess(st, recall_s=4.0, margin_s=1.0, now=NOW, params=Params(aggressive_bodies=frozenset({0x1D})))
    eq("aggressive_bodies param -> flee", one(a, 0x300).action, "flee")
    a = assess(st, recall_s=4.0, margin_s=1.0, now=NOW,
               params=Params(monster_default_aggressive=True, passive_names=frozenset({"a blood ape"})))
    eq("passive_names param -> ignore", one(a, 0x300).action, "ignore")
    a = assess(st, recall_s=4.0, margin_s=1.0, now=NOW,
               params=Params(monster_default_aggressive=True, monster_s_per_tile=0.2))
    eq("faster monsters param -> far ape flees (eta 3.8)", one(a, 0x303).action, "flee")


def test_pets():
    print("== pets and war-mode passive bodies (Shelter Island, session 20261003_111419) ==")
    # live: 'a phoenix' (832) and 'a gravebug' (387), notoriety 1, flags 0x40, "(bonded)",
    # one tile from their owner; their bodies are in aggressive_bodies once seen hostile
    phoenix, gravebug, owner = 0x0059D277, 0x011026C0, 0x00659472
    learned = Params(aggressive_bodies=frozenset({832, 387}))
    mobs = [mob(phoenix, 6, 0, body=832, noto=1, flags=0x40, pet="bonded"),
            mob(gravebug, 6, 0, body=387, noto=1, flags=0x40, pet="tame"),
            mob(owner, 6, 1, noto=1, flags=0x20)]
    labels = {phoenix: "a phoenix", gravebug: "a gravebug", owner: "Lord Arlabunakti"}
    kw = dict(recall_s=2.0, margin_s=1.0, now=NOW)
    a = assess(state(mobs, labels=labels), **kw, params=learned)
    eq("war-mode bonded / tame pets with learned-aggressive bodies -> not hostile, ignore",
       [(one(a, s).hostile, one(a, s).action) for s in (phoenix, gravebug)], [(False, "ignore")] * 2)
    wild = [mob(phoenix, 6, 0, body=832, noto=1, flags=0)]
    a = assess(state(wild, labels=labels), **kw, params=learned)
    eq("the same body without a pet tag (learned aggressive) -> flee", one(a, phoenix).action, "flee")
    a = assess(state(mobs, labels=labels, swings={phoenix: (ME, NOW - 1.0)}), **kw, params=learned)
    eq("a pet swinging at us -> flee", one(a, phoenix).action, "flee")
    red_pet = [mob(phoenix, 6, 0, body=832, noto=6, flags=0x40, pet="bonded")]
    a = assess(state(red_pet, labels=labels), **kw)
    eq("a murderer's pet (notoriety 6) -> flee", one(a, phoenix).action, "flee")


def test_damage():
    print("== damage -> under_attack ==")
    w = Watch()
    a = w.update(state(hits=50), recall_s=4.0, margin_s=1.0, now=NOW)
    eq("full hits -> not under attack", a.under_attack, False)
    a = w.update(state(hits=44), recall_s=4.0, margin_s=1.0, now=NOW + 2)
    eq("hits drop -> under attack", (a.under_attack, a.damage["lost"]), (True, 6))
    eq("under attack -> flee", a.action, "flee")
    check("reason mentions the attack", any("under attack" in r for r in a.reasons), a.reasons)
    a = w.update(state(hits=44), recall_s=4.0, margin_s=1.0, now=NOW + 30)
    eq("old drop outside the window -> calm", a.under_attack, False)
    a = w.update(state(hits=48), recall_s=4.0, margin_s=1.0, now=NOW + 31)
    eq("healing is not damage", a.under_attack, False)
    ev = [(NOW - 1, {"ev": "damage", "serial": ME, "amount": 3})]
    a = assess(state(events=ev), recall_s=4.0, margin_s=1.0, now=NOW)
    eq("damage event on self", (a.under_attack, a.damage["damage_events"]), (True, 1))
    ev = [(NOW - 1, {"ev": "damage", "serial": 0x999, "amount": 3})]
    eq("damage on someone else -> calm",
       assess(state(events=ev), recall_s=4.0, margin_s=1.0, now=NOW).under_attack, False)
    ev = [(NOW - 2, {"ev": "swing", "attacker": 0x100, "defender": ME})]
    a = assess(state(events=ev), recall_s=4.0, margin_s=1.0, now=NOW)
    eq("swing at self", (a.under_attack, a.damage["swings"]), (True, 1))
    ev = [(NOW - 60, {"ev": "swing", "attacker": 0x100, "defender": ME})]
    eq("old swing ignored",
       assess(state(events=ev), recall_s=4.0, margin_s=1.0, now=NOW).under_attack, False)
    ev = [(NOW - 1, {"ev": "damage", "serial": ME, "amount": 3})]
    a = assess(state(events=ev), recall_s=4.0, margin_s=1.0, now=NOW,
               params=Params(flee_on_attack=False))
    eq("flee_on_attack=False -> watch", a.action, "watch")
    eq("flee_on_attack=False reason kept", any("under attack" in r for r in a.reasons), True)


def test_label_grace():
    print("== unlabeled human: grace before the assumed-player reading ==")
    # juncture 44: a battle trainer's 0x20 (human, notoriety 3, war mode, no
    # player bit) was assessed 62 ms before its label arrived
    trainer = [mob(0x411866, -14, 18, noto=3, flags=0x40)]
    w = Watch()
    a = w.update(state(trainer), recall_s=2.0, margin_s=1.0, now=NOW)
    eq("unlabeled grey on first sight -> watch", (one(a, 0x411866).kind, a.action),
       ("grey", "watch"))
    check("reason says awaiting label", "awaiting label" in one(a, 0x411866).reason,
          one(a, 0x411866).reason)
    a = w.update(state(trainer, labels={0x411866: "Beaman the battle trainer"}),
                 recall_s=2.0, margin_s=1.0, now=NOW + 0.06)
    eq("label arrives -> npc ignore", (one(a, 0x411866).kind, a.action), ("npc", "ignore"))
    # no label within the grace: the conservative reading applies
    w = Watch()
    w.update(state(trainer), recall_s=2.0, margin_s=1.0, now=NOW)
    a = w.update(state(trainer), recall_s=2.0, margin_s=1.0, now=NOW + 0.99)
    eq("still unlabeled inside the grace -> watch", a.action, "watch")
    a = w.update(state(trainer), recall_s=2.0, margin_s=1.0, now=NOW + 1.0)
    eq("unlabeled after the grace -> flee", a.action, "flee")
    # player evidence or a non-title label gets no grace
    w = Watch()
    a = w.update(state([mob(0x500, 5, 0, noto=4, flags=0x20)]),
                 recall_s=2.0, margin_s=1.0, now=NOW)
    eq("player-bit grey on first sight -> flee", a.action, "flee")
    w = Watch()
    a = w.update(state([mob(0x501, 5, 0, noto=6, flags=0)], labels={0x501: "Killer"}),
                 recall_s=2.0, margin_s=1.0, now=NOW)
    eq("labeled untitled red on first sight -> flee", a.action, "flee")
    # a mobile that leaves the state and returns gets a fresh first sighting
    w = Watch()
    w.update(state(trainer), recall_s=2.0, margin_s=1.0, now=NOW)
    w.update(state(), recall_s=2.0, margin_s=1.0, now=NOW + 5)
    a = w.update(state(trainer), recall_s=2.0, margin_s=1.0, now=NOW + 5.5)
    eq("re-entry restarts the grace", a.action, "watch")


def test_fighting_others():
    print("== war-mode creature fighting someone else (knowledge #89) ==")
    # live: 'a great hart' 8 tiles away fighting a player; the hart's body is passive now,
    # so an unknown creature body stands in for the rule (war mode its only evidence)
    hart, other = 0x600, 0x601
    mobs = [mob(hart, 8, 0, body=0x27, noto=3, flags=0x40), mob(other, 9, 0, noto=1, flags=0x20)]
    labels = {hart: "a mongbat", other: "Vorn"}
    kw = dict(recall_s=2.0, margin_s=1.0, now=NOW)      # monster flee radius 1 + floor(3 / 0.4) = 8
    a = assess(state(mobs, labels=labels), **kw)
    eq("old capture (no world.swings): war-mode creature at 8 -> flee", one(a, hart).action, "flee")
    a = assess(state(mobs, labels=labels, swings={}), **kw)
    eq("no swing seen: war-mode creature at 8 -> flee", one(a, hart).action, "flee")
    busy = {hart: (other, NOW - 1.5), other: (hart, NOW - 1.0)}
    a = assess(state(mobs, labels=labels, swings=busy), **kw)
    eq("its latest swing at another player -> watch, overall watch",
       (one(a, hart).action, a.action, a.under_attack), ("watch", "watch", False))
    check("reason names its opponent", f"0x{other:08X}" in one(a, hart).reason, one(a, hart).reason)
    a = assess(state(mobs, labels=labels, swings={hart: (ME, NOW - 1.0)}), **kw)
    eq("its latest swing at us -> flee", one(a, hart).action, "flee")
    ev = [(NOW - 3, {"ev": "swing", "attacker": hart, "defender": ME})]
    a = assess(state(mobs, labels=labels, swings=busy, events=ev), **kw)
    eq("swung at us within the window, latest at another -> flee (and under attack)",
       (one(a, hart).action, a.under_attack, a.action), ("flee", True, "flee"))
    a = assess(state(mobs, labels=labels, swings={hart: (other, NOW - 30)}), **kw)
    eq("fight over (last swing 30 s ago) -> flee", one(a, hart).action, "flee")
    near = [mob(hart, 1, 0, body=0x27, noto=3, flags=0x40), mob(other, 2, 0, noto=1, flags=0x20)]
    a = assess(state(near, labels=labels, swings=busy), **kw)
    eq("fighting another but within its strike range of us -> flee", one(a, hart).action, "flee")
    ev = [(NOW - 1, {"ev": "damage", "serial": ME, "amount": 4})]
    a = assess(state(mobs, labels=labels, swings=busy, events=ev), **kw)
    eq("damage to us while it fights another -> overall flee (under attack)",
       (one(a, hart).action, a.under_attack, a.action), ("watch", True, "flee"))
    # war mode isn't its only aggression evidence: an aggressive body flees anyway
    a = assess(state(mobs, labels=labels, swings=busy), **kw,
               params=Params(aggressive_bodies=frozenset({0x27})))
    eq("aggressive body fighting another -> flee", one(a, hart).action, "flee")
    a = assess(state([mob(0x602, 6, 0, body=0x1D, noto=6, flags=0x40)], swings={0x602: (other, NOW - 1)}),
               **kw)
    eq("murderer-red creature fighting another -> flee", one(a, 0x602).action, "flee")


def test_acknowledge():
    print("== acknowledged damage: only new drops count ==")
    w = Watch()
    w.update(state(hits=50), recall_s=2.0, margin_s=1.0, now=NOW)
    a = w.update(state(hits=40), recall_s=2.0, margin_s=1.0, now=NOW + 1)
    eq("a drop is damage", a.damage["lost"], 10)
    w.acknowledge(now=NOW + 1)
    a = w.update(state(hits=40), recall_s=2.0, margin_s=1.0, now=NOW + 2)
    eq("the same hits after the acknowledgement: calm", (a.under_attack, a.damage["lost"]), (False, 0))
    a = w.update(state(hits=35), recall_s=2.0, margin_s=1.0, now=NOW + 3)
    eq("a further drop counts from the acknowledged hits", a.damage["lost"], 5)
    w.acknowledge(now=NOW + 3)
    ev = [(NOW + 2.5, {"ev": "damage", "serial": ME, "amount": 3})]
    eq("a damage event from before the acknowledgement is dealt with",
       w.update(state(hits=35, events=ev), recall_s=2.0, margin_s=1.0, now=NOW + 4).under_attack, False)
    w = Watch()
    w.update(state(hits=50), recall_s=2.0, margin_s=1.0, now=NOW)
    w.acknowledge(now=NOW + 0.1, hits=49)       # our own trapped pouch took one, not yet seen by an update
    a = w.update(state(hits=49), recall_s=2.0, margin_s=1.0, now=NOW + 0.2)
    eq("acknowledge(hits=): the drop to those hits is dealt with before any update saw it",
       (a.under_attack, a.damage["lost"]), (False, 0))


def test_steal_guard():
    print("== steal guard: any non-ally player within 2 tiles is a suspected thief (when the caller asks) ==")
    mobs = [mob(0x500, 1, 0),                                           # a blue player next to us
            mob(0x501, 2, 2, noto=2),                                   # a green one (guild/alliance) at 2
            mob(0x502, 3, 0),                                           # a blue at 3: watched
            mob(0x503, 1, 1, body=0xD9, noto=1, flags=0, pet="tame"),   # a pet dog
            mob(0x504, 0, 1, noto=7, flags=0),                          # a vendor
            mob(0x505, -1, 0, noto=4)]                                  # a grey: hostile, flees
    off = assess(state(mobs), recall_s=2.0, margin_s=1.0, now=NOW)
    eq("off by default: the blues are watched", (one(off, 0x500).action, off.thieves), ("watch", []))
    a = assess(state(mobs), recall_s=2.0, margin_s=1.0, now=NOW, params=Params(steal_guard=2))
    eq("steal_guard 2: the blue at 1 tile is `thief`, the green (an ally, user 2026-10-09) at 2 isn't, the one at "
       "3 `watch`", [(t.serial, t.action) for t in a.threats if t.serial in (0x500, 0x501, 0x502)],
       [(0x500, "thief"), (0x501, "watch"), (0x502, "watch")])
    eq("never a pet, a vendor; a hostile player keeps flee", (one(a, 0x503).action, one(a, 0x504).action,
                                                               one(a, 0x505).action), ("ignore", "ignore", "flee"))
    eq("the worst action: flee over thief", a.action, "flee")
    calm = assess(state(mobs[:3]), recall_s=2.0, margin_s=1.0, now=NOW, params=Params(steal_guard=2))
    eq("no hostile: the assessment says thief", (calm.action, [t.serial for t in calm.thieves]),
       ("thief", [0x500]))
    green = one(a, 0x501)
    green.faction = "Andaria"
    eq("a green player is friendly whatever faction tag he shows (no faction rule, no precast rule)",
       friendly(green, {"guild": None, "faction": None}), True)


def test_hit_attackers():
    print("== who hit us: melee range, else ranged within the spell range; never pets or passive bodies ==")
    p = Params()

    def who(mobs, swung=()):
        a = assess(state(mobs), recall_s=2.0, margin_s=1.0, now=NOW, params=p)
        out, ranged = hit_attackers(a, p, swung)
        return [t.serial for t in out], ranged
    calm = {"noto": 3, "flags": 0}
    eq("a gazer 10 tiles off, nothing adjacent: it, ranged",
       who([mob(0x501, 10, 0, body=22, **calm)]), ([0x501], True))
    eq("an unknown creature 11 tiles off (aggression unknown): ranged by default",
       who([mob(0x502, 11, 0, body=0x99, **calm)]), ([0x502], True))
    eq("beyond the spell range: nobody to blame",
       who([mob(0x503, CREATURE_SPELL_RANGE + 1, 0, body=0x99, **calm)]), ([], True))
    eq("an adjacent creature is the melee attacker; an unknown one 8 tiles off isn't counted",
       who([mob(0x504, 1, 1, body=0x27, **calm), mob(0x505, 8, 0, body=0x99, **calm)]), ([0x504], False))
    eq("adjacent melee plus a gazer within its reach: two attackers",
       sorted(who([mob(0x504, 1, 0, body=0x27, **calm), mob(0x501, 9, 0, body=22, **calm)])[0]), [0x501, 0x504])
    eq("a sheep and a bonded pet next to us are never blamed; the gazer is",
       who([mob(0x506, 1, 0, body=0xCF, **calm), mob(0x507, 1, 1, body=832, noto=1, flags=0x40, pet="bonded"),
            mob(0x501, 7, 0, body=22, **calm)]), ([0x501], True))
    eq("two ranged candidates: the ranged body first, both counted",
       who([mob(0x502, 4, 0, body=0x99, **calm), mob(0x501, 9, 0, body=22, **calm)]), ([0x501, 0x502], True))
    eq("one swinging at us 3 tiles off is the attacker",
       who([mob(0x508, 3, 0, body=0x27, **calm)], swung={0x508: NOW}), ([0x508], True))
    # juncture 222 (witcher_280): a war-mode gazer larva 10 off and a calm cougar 8 off; a raven
    eq("from afar, a hostile one in reach leaves out the unknown-aggression ones (juncture 222: was 2)",
       who([mob(0x42E6DB, 10, 0, body=778, noto=4, flags=0x40), mob(0x7031B, 8, 0, body=214, **calm),
            mob(0x904470, 8, 0, body=6, **calm)]), ([0x42E6DB], True))
    eq("...but a known-ranged body in reach stays a suspect next to the hostile one",
       sorted(who([mob(0x42E6DB, 10, 0, body=778, noto=4, flags=0x40), mob(0x501, 9, 0, body=22, **calm)])[0]),
       [0x501, 0x42E6DB])
    a = assess(state([mob(0x7031B, 8, 0, body=214, **calm)]), recall_s=2.0, margin_s=1.0, now=NOW)
    eq("a calm creature's reason names its aggression", one(a, 0x7031B).reason, "passive creature (default)")


def test_spells():
    print("== a spell landing on us is an attack (witcher_280, session 20261003_213125) ==")
    sysline = {"ev": "speech_heard", "serial": 0xFFFFFFFF, "name": "System", "type": 0, "hue": 946}
    reflect = {"ev": "effect", "type": 3, "source": ME, "target": ME, "graphic": 0x37B9}
    eq("'Magic reflect removed.' from System", spell_on_us({**sysline, "text": "Magic reflect removed."}, ME),
       (True, None))
    eq("'You absorb their spell.' and 'Spell siphon active.' too",
       [spell_on_us({**sysline, "text": t}, ME)[0] for t in ("You absorb their spell.", "Spell siphon active.")],
       [True, True])
    eq("the same words said by a player don't count",
       spell_on_us({**sysline, "serial": 0x1234, "text": "Magic reflect removed."}, ME), (False, None))
    eq("a fixed effect 0x37B9 on us (the reflect), 0x374A, flame strike 0x3709: unnamed caster",
       [spell_on_us({**reflect, "graphic": g}, ME) for g in (0x37B9, 0x374A, 0x3709)], [(True, None)] * 3)
    eq("lightning (type 1, graphic 0) on us", spell_on_us({**reflect, "type": 1, "graphic": 0}, ME), (True, None))
    eq("our own cast start (0), Magic Reflection up (0x375A), a fizzle (0x3735), a heal (0x376A), "
       "a cure (0x373A), the Harvest aspect's double-yield proc (0x37BE, live 2026-10-04): not attacks",
       [spell_on_us({**reflect, "graphic": g}, ME)[0] for g in (0, 0x375A, 0x3735, 0x376A, 0x373A, 0x37BE)],
       [False] * 6)
    eq("an effect on someone else (the bolt reflected onto the larva)",
       spell_on_us({**reflect, "type": 1, "source": 0x42E6DB, "target": 0x42E6DB, "graphic": 0}, ME), (False, None))
    eq("a moving effect at us names its caster (an arrow 0xF42)",
       spell_on_us({"ev": "effect", "type": 0, "source": 0x500, "target": ME, "graphic": 0xF42}, ME), (True, 0x500))
    larva = mob(0x42E6DB, 10, 0, body=778, noto=4, flags=0x40)
    w = Watch()
    a = w.update(state([larva]), recall_s=2.0, margin_s=1.0, now=NOW)
    eq("before: the war-mode larva at 10 is watched, nothing on us", (a.action, a.under_attack), ("watch", False))
    ev = [(NOW + 0.1, {**sysline, "text": "Magic reflect removed."}), (NOW + 0.1, reflect)]
    a = w.update(state([larva], events=ev), recall_s=2.0, margin_s=1.0, now=NOW + 0.2)
    eq("the spell: under attack with no hits lost, 2 spell events, flee",
       (a.under_attack, a.damage["lost"], a.damage["spells"], a.action), (True, 0, 2, "flee"))
    check("the reason counts the spells", any("2 spell events" in r for r in a.reasons), a.reasons)
    w.acknowledge(now=NOW + 0.2)
    eq("acknowledged: the same spells are dealt with",
       w.update(state([larva], events=ev), recall_s=2.0, margin_s=1.0, now=NOW + 1).under_attack, False)
    calm = mob(0x600, 9, 0, body=0x99, noto=3, flags=0)
    ev = [(NOW - 1, {"ev": "effect", "type": 0, "source": 0x600, "target": ME, "graphic": 0x36D4})]
    a = assess(state([calm], events=ev), recall_s=2.0, margin_s=1.0, now=NOW)
    eq("a calm creature whose fireball came at us: aggressive, casting at us",
       (one(a, 0x600).aggressive, one(a, 0x600).aggression), (True, "casting at us"))
    eq("...and the attacker hit_attackers names from `swung`",
       [t.serial for t in hit_attackers(a, Params(), {0x600: NOW - 1})[0]], [0x600])


def test_unseen_attackers():
    print("== who hit us from out of view: a creature that just left it (live 2026-10-08, witcher_86) ==")
    calm = {"noto": 3, "flags": 0}

    def gone(serial, dx, *, body=4, why="range", ago=1.0, facet=0):
        key, m = mob(serial, dx, 0, body=body, **calm)
        return key, {**m, "why": why, "t": NOW - ago, "facet": facet}

    def blamed(left, mobs=()):
        st = state(mobs)
        st["world"]["self"]["map"] = 0
        st["world"]["view_range"] = 18
        st["world"]["last_seen"] = dict(left)
        return [t.serial for t in unseen_attackers(st, Params(), now=NOW)]
    eq("a gargoyle pruned at the view edge 1 s ago, 19 tiles off: it", blamed([gone(0x700, 19)]), [0x700])
    eq("one the server deleted counts too", blamed([gone(0x700, 19, why="delete")]), [0x700])
    eq("not one that died, changed facet, left 31 s ago or is on another facet",
       blamed([gone(0x701, 19, why="dead"), gone(0x702, 19, why="facet"), gone(0x703, 19, ago=31.0),
               gone(0x704, 19, facet=1)]), [])
    eq("not one last seen beyond the view range + slack (25 > 18 + 6)", blamed([gone(0x705, 25)]), [])
    eq("not a passive body (a sheep)", blamed([gone(0x706, 19, body=0xCF)]), [])
    eq("not one back in view (mobiles has it)", blamed([gone(0x707, 19)], [mob(0x707, 19, 0, body=4, **calm)]), [])
    eq("two of them: both, nearest first", blamed([gone(0x708, 22), gone(0x709, 19)]), [0x709, 0x708])


TESTS = [test_reds, test_npcs_and_players, test_monsters, test_pets, test_damage, test_label_grace,
         test_fighting_others, test_acknowledge, test_steal_guard, test_hit_attackers, test_spells,
         test_unseen_attackers]


def main():
    for t in TESTS:
        t()
    print(f"\nthreats: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

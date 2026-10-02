"""Tests for harness/threats.py on synthetic state-port responses (no network).

Run: python harness/test_threats.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from threats import Params, Watch, assess, flee_radius  # noqa: E402

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


def mob(serial, dx, dy, *, body=0x190, noto=1, flags=0x20):
    return (f"0x{serial:08X}", {"graphic": body, "notoriety": noto, "flags": flags,
                                "x": HERE[0] + dx, "y": HERE[1] + dy, "z": 0})


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
    eq("sheep in war mode -> aggressive flee (default params)", one(a, 0x302).action, "flee")
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
    eq("war mode beats passive_bodies", one(a, 0x302).action, "flee")
    a = assess(st, recall_s=4.0, margin_s=1.0, now=NOW, params=Params(aggressive_bodies=frozenset({0x1D})))
    eq("aggressive_bodies param -> flee", one(a, 0x300).action, "flee")
    a = assess(st, recall_s=4.0, margin_s=1.0, now=NOW,
               params=Params(monster_default_aggressive=True, passive_names=frozenset({"a blood ape"})))
    eq("passive_names param -> ignore", one(a, 0x300).action, "ignore")
    a = assess(st, recall_s=4.0, margin_s=1.0, now=NOW,
               params=Params(monster_default_aggressive=True, monster_s_per_tile=0.2))
    eq("faster monsters param -> far ape flees (eta 3.8)", one(a, 0x303).action, "flee")


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
    # live: 'a great hart' (0xEA, notoriety 3, war mode) 8 tiles away fighting a player
    hart, other = 0x600, 0x601
    mobs = [mob(hart, 8, 0, body=0xEA, noto=3, flags=0x40), mob(other, 9, 0, noto=1, flags=0x20)]
    labels = {hart: "a great hart", other: "Vorn"}
    kw = dict(recall_s=2.0, margin_s=1.0, now=NOW)      # monster flee radius 1 + floor(3 / 0.4) = 8
    a = assess(state(mobs, labels=labels), **kw)
    eq("old capture (no world.swings): war-mode hart at 8 -> flee", one(a, hart).action, "flee")
    a = assess(state(mobs, labels=labels, swings={}), **kw)
    eq("no swing seen: war-mode hart at 8 -> flee", one(a, hart).action, "flee")
    busy = {hart: (other, NOW - 1.5), other: (hart, NOW - 1.0)}
    a = assess(state(mobs, labels=labels, swings=busy), **kw)
    eq("hart's latest swing at another player -> watch, overall watch",
       (one(a, hart).action, a.action, a.under_attack), ("watch", "watch", False))
    check("reason names its opponent", f"0x{other:08X}" in one(a, hart).reason, one(a, hart).reason)
    a = assess(state(mobs, labels=labels, swings={hart: (ME, NOW - 1.0)}), **kw)
    eq("hart's latest swing at us -> flee", one(a, hart).action, "flee")
    ev = [(NOW - 3, {"ev": "swing", "attacker": hart, "defender": ME})]
    a = assess(state(mobs, labels=labels, swings=busy, events=ev), **kw)
    eq("swung at us within the window, latest at another -> flee (and under attack)",
       (one(a, hart).action, a.under_attack, a.action), ("flee", True, "flee"))
    a = assess(state(mobs, labels=labels, swings={hart: (other, NOW - 30)}), **kw)
    eq("fight over (last swing 30 s ago) -> flee", one(a, hart).action, "flee")
    near = [mob(hart, 1, 0, body=0xEA, noto=3, flags=0x40), mob(other, 2, 0, noto=1, flags=0x20)]
    a = assess(state(near, labels=labels, swings=busy), **kw)
    eq("fighting another but within its strike range of us -> flee", one(a, hart).action, "flee")
    ev = [(NOW - 1, {"ev": "damage", "serial": ME, "amount": 4})]
    a = assess(state(mobs, labels=labels, swings=busy, events=ev), **kw)
    eq("damage to us while it fights another -> overall flee (under attack)",
       (one(a, hart).action, a.under_attack, a.action), ("watch", True, "flee"))
    # war mode isn't its only aggression evidence: an aggressive body flees anyway
    a = assess(state(mobs, labels=labels, swings=busy), **kw,
               params=Params(aggressive_bodies=frozenset({0xEA}), passive_bodies=frozenset()))
    eq("aggressive body fighting another -> flee", one(a, hart).action, "flee")
    a = assess(state([mob(0x602, 6, 0, body=0x1D, noto=6, flags=0x40)], swings={0x602: (other, NOW - 1)}),
               **kw)
    eq("murderer-red creature fighting another -> flee", one(a, 0x602).action, "flee")


TESTS = [test_reds, test_npcs_and_players, test_monsters, test_damage, test_label_grace,
         test_fighting_others]


def main():
    for t in TESTS:
        t()
    print(f"\nthreats: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

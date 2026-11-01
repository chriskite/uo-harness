"""The lumber runner's hatchet top-up at home (loop_lumber.LumberLoop.hatchets_ready): a worn hatchet
goes into the house chest for a fresh one from the shelf only while that gains uses. Live 2026-10-31
(lumber-20261031-220115-8cb4): a 10 000-log quota needs 2000 uses, a fresh exceptional verite hatchet
holds 1875, and the runner swapped full hatchet for full hatchet until the shelf ran dry (8 swaps).
A fake runner (no network, no game).

Run: python harness/test_lumber_hatchets.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import loop_lumber  # noqa: E402
import lumber_opt  # noqa: E402

FAILURES = []
VERITE_EXC = {"graphic": 0x0F43, "hue": 2207, "name": "exceptional verite hatchet", "container": "0x1"}
FRESH = 1875                    # the live label of a fresh one from the shelf


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f": {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class Fake:
    """The parts of LumberLoop hatchets_ready touches. `carried` {serial: uses}; the shelf's Resupply tops
    the carried hatchets up to `loadout` from `shelf` hatchets of `fresh` uses each; the chest holds what
    to_chest puts away and hands out the most uses first."""

    def __init__(self, carried, shelf, fresh=FRESH, logs=10000, loadout=1, chest=()):
        self.carried, self.shelf, self.fresh, self.loadout = dict(carried), shelf, fresh, loadout
        self.chest, self.swaps, self.next = list(chest), 0, 0x4E200000
        self.args = types.SimpleNamespace(logs_per_trip=logs, resupply="on")
        self.hatchets = lumber_opt.load_hatchets()
        self.pre_stats, self.trip_n, self.junctures = {}, 1, []
        self.memory = types.SimpleNamespace(juncture=lambda *a, **k: self.junctures.append(a))
        self.link = types.SimpleNamespace(state=lambda: None)

    JOB = loop_lumber.LumberLoop.JOB
    uses_needed = staticmethod(loop_lumber.LumberLoop.uses_needed)

    def state(self):
        return None

    def item(self, _st, _serial):
        return dict(VERITE_EXC)

    def hatchet_uses(self, _st, only=None):
        return {s: (n, "label") for s, n in self.carried.items() if only is None or s in only}

    def to_chest(self, items, what):
        for s, _ in items:
            self.chest.append((s, self.carried.pop(s)))
            self.swaps += 1

    def resupply_here(self, where):
        while len(self.carried) < self.loadout and self.shelf > 0 and self.swaps <= 50:   # bounded: no hang
            self.shelf -= 1
            self.carried[self.next] = self.fresh
            self.next += 1

    def hatchets_from_chest(self, need, uses):
        taken = []
        while sum(n for n, _ in uses.values()) < need and self.chest:
            s, n = max(self.chest, key=lambda c: c[1])
            self.chest.remove((s, n))
            self.carried[s] = n
            uses[s] = (n, "label")
            taken.append({"serial": f"0x{s:08X}", "uses": n})
        return taken


def run(fake):
    loop_lumber.LumberLoop.hatchets_ready(fake, at_home=True)
    return fake


def main():
    # the live case: one fresh hatchet, a one-hatchet loadout, a 2000-use quota: one more from the shelf,
    # the parked one back (before: 8 swaps until the shelf ran dry, every hatchet left in the chest)
    f = run(Fake({0x4E169E11: FRESH}, shelf=10))
    check("live case: one round", f.swaps == 1, f"{f.swaps} swaps")
    check("live case: one hatchet off the shelf", f.shelf == 9, f"shelf {f.shelf}")
    check("live case: two fresh carried", sorted(f.carried.values()) == [FRESH, FRESH], str(f.carried))
    check("live case: chest empty", f.chest == [], str(f.chest))
    check("live case: no low_supplies", not f.junctures, str(f.junctures))

    # the chest's spare first: the shelf untouched
    f = run(Fake({0x4E169E11: FRESH}, shelf=10, chest=[(0x4E138675, FRESH)]))
    check("chest spare: no swap", f.swaps == 0, f"{f.swaps} swaps")
    check("chest spare: shelf untouched", f.shelf == 10, f"shelf {f.shelf}")
    check("chest spare: taken", f.pre_stats["hatchets"]["have"] == 2 * FRESH, str(f.pre_stats["hatchets"]))

    # the shelf out of hatchets: the parked one comes back, one round, low_supplies, the trip goes on
    f = run(Fake({0x4E169E11: FRESH}, shelf=0))
    check("shelf empty: one round", f.swaps == 1, f"{f.swaps} swaps")
    check("shelf empty: the hatchet back in the pack", list(f.carried.values()) == [FRESH], str(f.carried))
    check("shelf empty: low_supplies", any(a[1] == "low_supplies" for a in f.junctures), str(f.junctures))

    # the user's two-hatchet loadout (2026-10-31): enough as carried
    f = run(Fake({1: FRESH, 2: FRESH}, shelf=10, loadout=2))
    check("loadout 2: no swap", f.swaps == 0 and f.shelf == 10, f"{f.swaps} swaps, shelf {f.shelf}")

    # a quota beyond the loadout: both parked, the shelf's two, then the chest's best back (7000 uses: four)
    f = run(Fake({1: FRESH, 2: FRESH}, shelf=10, loadout=2, logs=35000))
    check("big quota: two parked, two off the shelf", f.swaps == 2 and f.shelf == 8, f"{f.swaps} parked, shelf {f.shelf}")
    check("big quota: enough", f.pre_stats["hatchets"]["have"] == 4 * FRESH, str(f.pre_stats["hatchets"]))

    # a worn one, a small quota: the fresh one is enough, the worn one stays in the chest
    f = run(Fake({0x4E000001: 100}, shelf=10, logs=5000))
    check("worn hatchet, small quota: swapped and enough", f.swaps == 1 and f.pre_stats["hatchets"]["have"] == FRESH,
          f"{f.swaps} swaps, {f.pre_stats['hatchets']}")

    print(f"\n{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

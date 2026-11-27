"""Damage on Logan by attacker in live captures, beside the simulator's `player_damage_by` (docs/SIM.md "Mount Petram:
fixing the gaps (2026-11-27)").

    python harness/sim/dmg_scan.py --scenario petram --tags 20261125_150950,20261127_111908 \
        [--window 2026-11-26T22:21:00/2026-11-26T23:21:00 ...] [--area x0,y0,x1,y1]
    python harness/sim/dmg_scan.py --sim BATCH.jsonl      # run.py --json: the simulator's, per hour on the level

Each capture is replayed through `WorldRuntime` (`replay.timed_packets`). Counted: every '-N' (speech type 0) over
Logan while he is alive in the area (`--area`, else the scenario's `area`) and inside a `--window` (none: all of it);
hours: one-second samples of the same. Each number goes to one attacker, the first rule that fits:
- **special:** a scenario special with an `action` whose type's animation came `delay_s` (+- SPECIAL_TOL_S, for a
  `repeat` also each `repeat_s` later) before, by one within its `radius` + `reach` (+ 1) of Logan;
- **ground:** an item of GROUND's graphics put down within GROUND_R of Logan within GROUND_S before (the fungaloid's
  spores: three ticks 0.76 s apart);
- **melee:** the latest animation of a hostile within 1 of Logan within MELEE_S before;
- **ranged:** the latest animation of a hostile within RANGED_R within RANGED_S before (an animation spammer, a mobile
  with SPAM_N or more in the last second, skipped: the fungaloid's action 4 comes every 0.05 s);
- **red player:** the same for a player (notoriety 6 or a human body);
- **poison** while he is poisoned, else **unknown**.
The ranged rule is a guess where several hostiles animate at once [INFERENCE]; the table shows the kind of each.
"""
import argparse
import collections
import datetime
import json
import os
import sys

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HARNESS)
sys.path.insert(0, HARNESS)

LOGAN = 0x00104A60
SPECIAL_TOL_S = 0.2
GROUND = {27854: "fungaloid"}      # 0x6CCE: the fungaloid's spore cloud on its target's tile (docs/NOTES.md)
GROUND_R = 2
GROUND_S = 1.9
MELEE_S = 1.0
RANGED_R = 12
RANGED_S = 2.5
SPAM_N = 5
HUMAN = {0x190, 0x191, 0x25D, 0x25E, 0x29A, 0x29B, 0x2EE, 0x2EF}


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def type_key(name, keys):
    n = (name or "").lower()
    for a in ("a ", "an ", "the "):
        if n.startswith(a):
            n = n[len(a):]
    return n if n in keys else None


def specials_by_action(sc) -> dict:
    """{(type, action): [(delay_s + i * repeat_s, reach)]} for the scenario's specials with an animation."""
    out = collections.defaultdict(list)
    for kind, sps in (sc.get("specials") or {}).items():
        for sp in sps if isinstance(sps, list) else ():
            if sp.get("action") is None:
                continue
            delay = sp.get("delay_s", 0.0)
            delay = sum(delay) / 2 if isinstance(delay, list) else delay
            reach = (sp.get("reach") or 0) + (sp.get("radius") or 0) + 1
            for i in range(sp.get("repeat", 1)):
                out[(kind, sp["action"])].append((delay + i * sp.get("repeat_s", 0.0), reach))
    return out


def scan(tag, keys, specials, area, windows):
    from replay import timed_packets
    from world.runtime import WorldRuntime
    from world.state import GHOST_BODIES
    now = [0.0]
    rt = WorldRuntime(clock=lambda: now[0])
    st = rt.state
    anims = collections.deque(maxlen=600)          # (t, serial, who, action, pos)
    ground = collections.deque(maxlen=50)          # (t, who, pos)
    dmg, nums, kinds = collections.Counter(), collections.Counter(), collections.Counter()
    seconds, nxt = 0, 0.0
    for t, _d, _s, p in timed_packets(os.path.join(REPO, "logs", "session_" + tag)):
        now[0] = t
        rt.feed_packet(_d, p)
        me = st.self
        alive = me.x is not None and getattr(me, "graphic", None) not in GHOST_BODIES
        on = alive and area[0] <= me.x <= area[2] and area[1] <= me.y <= area[3] \
            and (not windows or any(lo <= t <= hi for lo, hi in windows))
        for e in rt.drain_events():
            ev = e["ev"]
            if ev == "animation" and e["serial"] != me.serial:
                m = st.mobiles.get(e["serial"])
                if m is not None and m.x is not None:
                    who = "red player" if m.notoriety == 6 or (m.graphic in HUMAN and m.notoriety not in (1, 2)) \
                        else type_key(m.name, keys)
                    if who:
                        anims.append((t, e["serial"], who, e["action"], (m.x, m.y)))
            elif ev == "item_seen" and e.get("graphic") in GROUND:
                it = st.items.get(e.get("serial"))
                if it is not None and getattr(it, "x", None) is not None:
                    ground.append((t, GROUND[e["graphic"]], (it.x, it.y)))
            elif ev == "speech_heard" and on and e["serial"] == me.serial and e["type"] == 0:
                text = e["text"] or ""
                if not (text.startswith("-") and text[1:].isdigit()):
                    continue
                who, kind = attribute(t, (me.x, me.y), anims, ground, specials, bool(me.poisoned))
                dmg[who] += int(text[1:])
                nums[who] += 1
                kinds[(who, kind)] += int(text[1:])
        if on and t >= nxt:
            nxt = t + 1.0
            seconds += 1
    return seconds, dmg, nums, kinds


def attribute(t, me, anims, ground, specials, poisoned):
    recent = [a for a in anims if t - RANGED_S - 3.0 <= a[0] <= t]
    for at, s, who, action, pos in reversed(recent):
        for delay, reach in specials.get((who, action), ()):
            if abs(t - at - delay) <= SPECIAL_TOL_S and cheb(pos, me) <= reach:
                return who, "special"
    for at, who, pos in reversed(ground):
        if 0 <= t - at <= GROUND_S and cheb(pos, me) <= GROUND_R:
            return who, "ground"
    window = [a for a in recent if t - RANGED_S <= a[0] <= t]
    spam = collections.Counter(s for at, s, *_ in recent if t - 1.0 <= at <= t)
    for at, s, who, action, pos in reversed(window):
        if t - at <= MELEE_S and cheb(pos, me) <= 1:
            return who, "melee"
    for at, s, who, action, pos in reversed(window):
        if spam[s] < SPAM_N and cheb(pos, me) <= RANGED_R:
            return who, "ranged"
    return ("poison", "poison") if poisoned else ("unknown", "unknown")


def window(s):
    lo, hi = s.split("/")
    return tuple(datetime.datetime.fromisoformat(x).timestamp() for x in (lo, hi))


def print_table(seconds, dmg, nums=None, kinds=None):
    """The attacker table; `nums` and `kinds` None for the simulator's (run.py keeps damage only)."""
    tot = sum(dmg.values())
    print(f"{seconds} s ({seconds / 3600:.2f} h) on the level, {tot} on Logan, {round(tot * 3600 / max(seconds, 1))} an hour")
    print(f"| attacker | damage | numbers | an hour | share | by |\n|---|---|---|---|---|---|")
    for who, v in dmg.most_common():
        by = ", ".join(f"{k} {n}" for (w, k), n in sorted((kinds or {}).items(), key=lambda kv: -kv[1]) if w == who)
        n = nums[who] if nums is not None else "-"
        print(f"| {who} | {round(v)} | {n} | {round(v * 3600 / max(seconds, 1))} | {100 * v / tot:.1f} % | {by} |")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scenario")
    ap.add_argument("--tags", help="capture tags, comma-separated")
    ap.add_argument("--window", action="append", default=[], help="ISO start/end, e.g. 2026-11-27T11:19/2026-11-27T12:51")
    ap.add_argument("--area", help="x0,y0,x1,y1 (default: the scenario's area)")
    ap.add_argument("--sim", help="run.py --json output: the simulator's player_damage_by an hour on the level")
    a = ap.parse_args()
    if a.sim:
        runs = [json.loads(line) for line in open(a.sim, encoding="utf-8") if line.strip()]
        runs = [r for r in runs if r.get("seed") is not None and r.get("player_damage_by") is not None]
        hunt = sum(r["hunt_s"] for r in runs)
        dmg = collections.Counter()
        for r in runs:
            dmg.update(r["player_damage_by"])
        print_table(int(hunt), dmg)
        return
    from sim import scenario
    sc = scenario.load(a.scenario)
    keys = set(sc["creatures"])
    area = tuple(map(int, a.area.split(","))) if a.area else tuple(sc["area"])
    windows = [window(w) for w in a.window]
    total = [0, collections.Counter(), collections.Counter(), collections.Counter()]
    for tag in a.tags.split(","):
        s, d, n, k = scan(tag, keys, specials_by_action(sc), area, windows)
        total[0] += s
        for i, c in enumerate((d, n, k), 1):
            total[i].update(c)
    print_table(*total)


if __name__ == "__main__":
    main()

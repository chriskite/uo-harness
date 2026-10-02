"""Measure Laya speech triage (triage.py) on a labeled set, through the
running laya-serve and the same prompt the runners send.

  python harness/triage.py serve          # in another terminal
  python harness/eval_triage.py [--url http://127.0.0.1:25970]

Label 1 = the line needs us (a GM-style check or a line aimed at us); 0 = a
player we could ignore. The 0 lines are real (memory DB, 2026-09-27..10-01:
guild ads in English/Turkish/French, two players talking to each other,
emotes, pet and NPC keywords). The 1 lines are written by us: no staff speech
has been captured yet. Add real lines as `speech_clear` job events accumulate.

Prints, per question, the score range of each label and:
- check: what ESCALATE_CHECK raises (GM-style lines caught, player lines alarmed)
- direct: the highest threshold that keeps every 1 line held, and how many
  0 lines it would let through. Fitted in-sample: an upper bound, not a
  promise (docs/PLAN.md "Laya speech triage").
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import triage  # noqa: E402

ME = "TestWorth"
K = [("Kemp", 2)]
CONVO = [("Mix-Alia", 3), ("onesevenone", 2)]
# (label, [(speaker, line), ...] ending with the judged line, [(name, tiles) near us])
CASES = [
    (1, [("Kemp", "Hello?")], K), (1, [("Kemp", "hello TestWorth")], K), (1, [("Kemp", "are you there?")], K),
    (1, [("Kemp", "hey, you at your keyboard?")], K), (1, [("Kemp", "what are you doing?")], K),
    (1, [("Kemp", "please respond")], K), (1, [("Kemp", "Can you say the word apple for me?")], K),
    (1, [("Kemp", "This is a GM, please answer.")], K), (1, [("Kemp", "how goes the chopping?")], K),
    (1, [("Kemp", "are you afk?")], K), (1, [("Kemp", "hey woodcutter")], K), (1, [("Kemp", "you macroing?")], K),
    (1, [("Kemp", "can you walk over to me please")], K), (1, [("Kemp", "yo")], K), (1, [("Kemp", "u there?")], K),
    (1, [("Kemp", "hey you")], K), (1, [("Kemp", "can i have some logs?")], K), (1, [("Kemp", "excuse me")], K),
    (1, [("Kemp", "hola, estas ahi?")], K), (1, [("Kemp", "bist du da?")], K), (1, [("Kemp", "hi")], K),
    (1, [("Kemp", "hello"), ("Kemp", "hello?"), ("Kemp", "anyone home")], K),
    (1, [("Kemp", "nice axe")], K), (1, [("Kemp", "*waves at TestWorth*")], K),
    (1, [("Kemp", "Mix-Alia come here"), ("Kemp", "and you too, lumberjack")], [("Kemp", 3), ("Mix-Alia", 4)]),
    (0, [("Hybrid Gatherer", "[EoB] Regular Dungeon Farms! Find a group, gain EXP and progress your characters!")],
     [("Hybrid Gatherer", 5)]),
    (0, [("Hybrid Gatherer", "Tek basina dungeon farm yapma! Aktif Dungeon ekiplerimize katil, EXP kazan ve "
                             "karakterini gelistir!")], [("Hybrid Gatherer", 5)]),
    (0, [("Hybrid Gatherer", "Interested in Eclipse Brotherhood [EoB]? Discord: lykia41burkay")], [("Hybrid Gatherer", 5)]),
    (0, [("Tommy Hilfiger'", "Recruiting new members for E<>E Guild we do all content of Outlands message on "
                             "discord @wheelz19")], [("Tommy Hilfiger'", 6)]),
    (0, [("Duracelle", "Joins la Guilde [QC]!")], [("Duracelle", 4)]),
    (0, [("Duracelle", "Discord: Bill#08088 pour en savoir plus!")], [("Duracelle", 4)]),
    (0, [("Ambrianna Dawn", "*hiking to destination*")], [("Ambrianna Dawn", 7)]),
    (0, [("Mix-Alia", "All follow")], [("Mix-Alia", 3)]),
    (0, [("onesevenone", "i want to level yp my knight without rushing. any tips ?"),
         ("Mix-Alia", "Need to go outside to reach 100.")], CONVO),
    (0, [("Mix-Alia", "You know where the battletraine is ?"), ("onesevenone", "no ")], CONVO),
    (0, [("onesevenone", "no "), ("Mix-Alia", "Come")], CONVO),
    (0, [("Mix-Alia", "Want me to go buy you a real horse ?")], CONVO),
    (0, [("Mix-Alia", "Want me to go buy you a real horse ?"), ("onesevenone", "you can ?")], CONVO),
    (0, [("onesevenone", "you are a friend "), ("onesevenone", "very tks ")], CONVO),
    (0, [("WorkWork", "say 1")], [("WorkWork", 8)]),
    (0, [("Kanbalt", "bank")], [("Kanbalt", 3)]), (0, [("Kanbalt", "vendor buy")], [("Kanbalt", 3)]),
    (0, [("Kanbalt", "guards")], [("Kanbalt", 3)]), (0, [("Kanbalt", "*yawns*")], [("Kanbalt", 3)]),
    (0, [("Kanbalt", "all kill")], [("Kanbalt", 3)]), (0, [("Kanbalt", "ugh this server lag")], [("Kanbalt", 3)]),
    (0, [("Kanbalt", "selling 1000 iron ingots, pst")], [("Kanbalt", 9)]),
]


def case_world(near):
    mobiles = {f"0x{i + 1:08X}": {"graphic": 0x190, "notoriety": 1, "flags": 0x20, "x": 100 + d, "y": 100, "name": n}
               for i, (n, d) in enumerate(near)}
    return {"self": {"serial": "0x00094375", "name": ME, "x": 100, "y": 100}, "mobiles": mobiles, "labels": {}}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=triage.DEFAULT_URL)
    a = ap.parse_args()
    t = triage.Triage(a.url, timeout=30.0)
    rows = []
    for label, lines, near in CASES:
        who = {"serial": "0x00000001", "text": lines[-1][1], "evidence": [],
               "context": [{"name": n, "text": x} for n, x in lines]}
        v = t.judge(who, case_world(near))
        if "error" in v:
            print(f"laya-serve error: {v['error']}")
            return 1
        rows.append((label, who["text"], v))
        print(f"{label} check {v['check']:.3f} direct {v['direct']:.3f} {v['ms']:4d} ms  {who['text'][:70]!r}")
    for q in ("check", "direct"):
        pos = [v[q] for y, _, v in rows if y == 1]
        neg = [v[q] for y, _, v in rows if y == 0]
        print(f"{q}: label 1 {min(pos):.3f}..{max(pos):.3f}, label 0 {min(neg):.3f}..{max(neg):.3f}")
    caught = [x for y, x, v in rows if y == 1 and v["check"] >= triage.ESCALATE_CHECK]
    alarmed = [x for y, x, v in rows if y == 0 and v["check"] >= triage.ESCALATE_CHECK]
    print(f"escalate at check >= {triage.ESCALATE_CHECK}: {len(caught)}/{sum(y for y, _, _ in rows)} label-1 lines "
          f"{caught}; {len(alarmed)} label-0 alarms {alarmed}")
    floor = min(v["direct"] for y, _, v in rows if y == 1)
    through = sum(1 for y, _, v in rows if y == 0 and v["direct"] < floor)
    print(f"direct: an ignore threshold of {floor:.3f} would hold every label-1 line and let "
          f"{through}/{sum(1 - y for y, _, _ in rows)} label-0 lines through (in-sample)")
    ms = sorted(v["ms"] for _, _, v in rows)
    print(f"latency: median {ms[len(ms) // 2]} ms, max {ms[-1]} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())

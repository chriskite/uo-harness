"""Measure Laya speech triage (triage.py) on a labeled set, through the
running laya-serve and the same prompt the runners send.

  python harness/triage.py serve          # in another terminal
  python harness/eval_triage.py [--url http://127.0.0.1:25970] [--repeat N] [--save F.json] [--compare F.json]
      [--heldout harness/data/triage/heldout.jsonl | --heldout ""]

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
- where the checkpoint runs (laya-serve /health) and latency: `ms` is the
  runner's round trip, `infer_ms` the server's forward pass. `--repeat` runs
  the set N more times for steadier latency figures (scores from the first).
- `--compare`: per-line score changes against a `--save`d run (another device,
  checkpoint or prompt) and which lines cross ESCALATE_CHECK.
- `--heldout`: real in-game player lines that the fine-tuning set
  (triage_data.py) holds out, with their real context; all are players, so
  every one at or above ESCALATE_CHECK is a false staff alarm. Used when the
  file exists (it holds real player lines, so it's gitignored: rebuild it with
  triage_data.py). These, not the GM-style lines we wrote, are the hard check.
"""
import argparse
import json
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


def case_world(near, me=ME):
    mobiles = {f"0x{i + 1:08X}": {"graphic": 0x190, "notoriety": 1, "flags": 0x20, "x": 100 + d, "y": 100, "name": n}
               for i, (n, d) in enumerate(near)}
    return {"self": {"serial": "0x00094375", "name": me, "x": 100, "y": 100}, "mobiles": mobiles, "labels": {}}


HELDOUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "triage", "heldout.jsonl")


def load_heldout(path):
    """(direct label or None, lines, near, me) per held-out real line; every one is a player."""
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(ln) for ln in f]
    return [(r["direct"], [tuple(x) for x in r["lines"]], [tuple(x) for x in r["near"]], r["me"]) for r in rows]


def compare(path, sets):
    """Per-line changes against a --save'd run: frozen rows by position, held-out rows by text."""
    with open(path, encoding="utf-8") as f:
        old = json.load(f)
    th = triage.ESCALATE_CHECK
    for name, new, prev in (("frozen", sets["frozen"], old["rows"]),
                            ("heldout", sets["heldout"], old.get("heldout") or [])):
        by_text = {o[1]: o for o in prev}
        pairs = [(n, prev[i] if name == "frozen" and i < len(prev) and prev[i][1] == n[1] else by_text.get(n[1]))
                 for i, n in enumerate(new)]
        pairs = [(n, o) for n, o in pairs if o is not None]
        if not pairs:
            print(f"vs {path}: no {name} rows to compare")
            continue
        print(f"== vs {path}, {name} set ({len(pairs)} lines): label, check old -> new, direct old -> new ==")
        for n, o in pairs:
            mark = " CROSSES" if (n[2]["check"] >= th) != (o[2]["check"] >= th) else ""
            print(f"  {n[0]!s:4} check {o[2]['check']:.3f} -> {n[2]['check']:.3f} ({n[2]['check'] - o[2]['check']:+.3f})"
                  f"  direct {o[2]['direct']:.3f} -> {n[2]['direct']:.3f} ({n[2]['direct'] - o[2]['direct']:+.3f})"
                  f"{mark}  {n[1][:60]!r}")
        for q in ("check", "direct"):
            d = [abs(n[2][q] - o[2][q]) for n, o in pairs]
            print(f"{name} {q}: max |delta| {max(d):.4f}, mean {sum(d) / len(d):.4f}")
        up = [n[1] for n, o in pairs if n[2]["check"] >= th > o[2]["check"]]
        down = [n[1] for n, o in pairs if o[2]["check"] >= th > n[2]["check"]]
        print(f"{name}: now at/above ESCALATE_CHECK {up or 'none'}; now below {down or 'none'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=triage.DEFAULT_URL)
    ap.add_argument("--repeat", type=int, default=0)
    ap.add_argument("--save", help="write the per-line verdicts here (JSON)")
    ap.add_argument("--compare", help="a --save'd run to diff the scores against")
    ap.add_argument("--heldout", default=HELDOUT, help="held-out real player lines (JSONL); '' to skip")
    a = ap.parse_args()
    try:
        h = triage.health(a.url)
    except OSError as e:
        print(f"laya-serve not reachable: {e}")
        return 1
    print(f"laya-serve health: {json.dumps(h)}")
    t = triage.Triage(a.url, timeout=30.0)

    def run(label, lines, near, me=ME):
        who = {"serial": "0x00000001", "text": lines[-1][1], "evidence": [],
               "context": [{"name": n, "text": x} for n, x in lines]}
        v = t.judge(who, case_world(near, me))
        if "error" in v:
            raise SystemExit(f"laya-serve error: {v['error']}")
        rows.append((label, who["text"], v))
        timed.append(v)
        return v

    rows, timed = [], []
    for label, lines, near in CASES:
        v = run(label, lines, near)
        print(f"{label} check {v['check']:.3f} direct {v['direct']:.3f} {v['ms']:4d} ms  {lines[-1][1][:70]!r}")
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
    frozen = [(y, x, {k: v[k] for k in ("check", "direct")}) for y, x, v in rows]

    held = load_heldout(a.heldout)
    if held:
        rows = []
        for direct, lines, near, me in held:
            v = run(direct, lines, near, me)
            print(f"H{direct!s:4} check {v['check']:.3f} direct {v['direct']:.3f}  {lines[-1][1][:70]!r}")
        alarms = [x for _, x, v in rows if v["check"] >= triage.ESCALATE_CHECK]
        checks = [v["check"] for _, _, v in rows]
        print(f"held-out real player lines: {len(rows)}, check {min(checks):.3f}..{max(checks):.3f}; false staff "
              f"alarms at check >= {triage.ESCALATE_CHECK}: {len(alarms)} {alarms}")
        for y in (1, 0):
            ds = [v["direct"] for d, _, v in rows if d == y]
            if ds:
                print(f"held-out direct, lines labelled {y} (aimed at us = 1): {min(ds):.3f}..{max(ds):.3f} (n={len(ds)})")
    heldout = [(y, x, {k: v[k] for k in ("check", "direct")}) for y, x, v in rows] if held else []

    for _ in range(a.repeat):
        for _, lines, near in CASES:
            run(None, lines, near)
    for key in ("ms", "infer_ms"):
        xs = sorted(v[key] for v in timed if v.get(key) is not None)
        if xs:
            print(f"{key}: median {xs[len(xs) // 2]}, p90 {xs[int(len(xs) * 0.9)]}, max {xs[-1]} (n={len(xs)})")
    if a.save:
        with open(a.save, "w", encoding="utf-8") as f:
            json.dump({"health": h, "rows": frozen, "heldout": heldout}, f, ensure_ascii=False, indent=1)
    if a.compare:
        compare(a.compare, {"frozen": frozen, "heldout": heldout})
    return 0


if __name__ == "__main__":
    sys.exit(main())

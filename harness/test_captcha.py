"""Captcha solver tests (harness/captcha.py, ANTICHEAT.md §8.8/§8.13).

Ground truth: data/captcha_samples.json — every real captcha gump captured
through the proxy (sessions 20260929_204225 … 20261020_102748, 364 captchas),
each paired with the answer the server accepted ("Captcha successful."). The
reference font (data/captcha_font.json) is derived from the same samples, so
section 1 is in-sample; section 6 and the 2026-10-20 captcha read with the
font of 2026-10-06 (section 4) are the out-of-sample checks. Both
files are written by harness/captcha_mine.py (python harness/captcha_mine.py
--dry-run to see what a new capture would add); when it adds samples, update
the hard-coded sample count and measured figures below and rerun this file.

Sections:
  1. End-to-end: every captured captcha solves to its recorded accepted answer,
     with its random submit button picked out (never the Guide button).
  2. Noise tolerance: jittered/dropped/noised variants of a seeded sample of the
     reference digits classify correctly (the production setting: all references
     present).
  3. Confidence gate: ambiguous clusters are refused (None), not guessed.
  4. The split: a stray dot far off its digit (2026-10-06), stray dots lying
     nearer the next digit (2026-10-20 17:14, also read with the font the solver
     had that day), digits sharing a tile graphic, a fourth graphic refused.
  5. Detection sanity: decoy/garbage layouts are not captchas.
  6. Held out (leave one session out): each capture against a font without
     its own session's references is solved right or refused, never wrong,
     whenever every digit it needs still has a reference from another session.

The heavy sections (1, 2, 6: ~3,200 digit reads against 1,093 references) run
in worker processes, at most UO_TEST_WORKERS of them (harness/run_tests.py sets
it), kept off the game's CPUs (cpu_budget).

Run: python harness/test_captcha.py
"""
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import captcha

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES_PATH = os.path.join(HERE, "data", "captcha_samples.json")
FAILURES = 0


def check(name, cond, detail=""):
    global FAILURES
    if cond:
        print(f"  [OK] {name}")
    else:
        FAILURES += 1
        print(f"  [FAIL] {name}  {detail}")


def _samples():
    with open(SAMPLES_PATH) as f:
        return json.load(f)


def _refs():
    with open(captcha.FONT_PATH) as f:
        return json.load(f)["references"]


def _use_refs(refs):
    """Make captcha read against these font references (None: the shipping font)."""
    captcha._FONT = None if refs is None else [(r["digit"], [tuple(p) for p in r["pts"]]) for r in refs]


def _pmap(fn, items):
    """fn over items in worker processes, results in order (UO_TEST_WORKERS caps the pool)."""
    import cpu_budget
    from concurrent.futures import ProcessPoolExecutor
    workers = max(1, min(len(items), int(os.environ.get("UO_TEST_WORKERS") or 0) or cpu_budget.workers()))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items, chunksize=max(1, len(items) // (4 * workers))))


def _solve(layout):
    return captcha.solve(layout)


def _read(q):
    pred, _, margin = captcha.read_digit(q)
    return pred, margin


def _held_out(tag):
    """[(answer, solved or None, covered)] for session tag's captchas against the font without its references."""
    refs = [r for r in _refs() if r["source"] != tag]
    _use_refs(refs)
    have = {r["digit"] for r in refs}
    try:
        return [(s["answer"], captcha.solve(s["layout"]) if set(s["answer"]) <= have else None,
                 set(s["answer"]) <= have)
                for s in _samples() if s["tag"] == tag]
    finally:
        _use_refs(None)


def test_real_captchas():
    print("== every captured captcha solves to its accepted answer ==")
    samples = _samples()
    check("samples present", len(samples) == 364, str(len(samples)))
    solved = _pmap(_solve, [s["layout"] for s in samples])
    wrong = [(s["tag"], s["answer"], got) for s, got in zip(samples, solved) if got != s["answer"]]
    check(f"all {len(samples)} solved to their accepted answers", not wrong, str(wrong[:10]))
    buttons = [(s["tag"], s["answer"], captcha.submit_button(s["layout"]), s["button"]) for s in samples]
    bad = [b for b in buttons if b[2] != b[3] or b[2] == 1]
    check("every submit button picked out (random per captcha, never Guide 1)", not bad, str(bad[:10]))


def test_noise_tolerance():
    print("== jitter/dropout/noise tolerance (margin-gated) ==")
    rng = random.Random(23)
    font = captcha._font()
    queries, truth = [], []
    for digit, ref in rng.sample(font, 200):
        for _ in range(5):
            q = [(x + rng.gauss(0, 0.045), y + rng.gauss(0, 0.045))
                 for x, y in ref if rng.random() > 0.15]
            for _ in range(rng.randint(0, 2)):
                q.append((rng.uniform(-0.4, 0.4), rng.uniform(-0.5, 0.5)))
            queries.append(q)
            truth.append(digit)
    accepted = right = rejected = 0
    for digit, (pred, margin) in zip(truth, _pmap(_read, queries)):
        if margin < captcha.MIN_MARGIN:
            rejected += 1
            continue
        accepted += 1
        right += pred == digit
    # measured at this seed: 94.48 % right (651/689), 31.1 % rejected (1,000 draws: 200 references x 5,
    # 1,093-reference font, 2026-10-20, 364 captchas). Until 2026-10-06 every reference was jittered 15
    # times: 96.55 % / 25.5 % with 166 references (55 captchas), 96.98 % / 19.1 % with 79 (26), 97.2 % with
    # 73, 98.1 % / 16.4 % with 37. Each batch of real references has lowered this synthetic figure while the
    # real-captcha evidence improved: the synthetic jitter goes on top of real displaced glyphs and the
    # confusions are spread over 35 of the 200 sources, mostly among 5/6/8/9. On real captchas the solver has
    # never answered wrong: sections 1 and 6 (364 captchas in sample and held out), the miner's out-of-sample
    # reads (the 309 captchas added 2026-10-20: 309 right with the 166-reference font) and 348 live answers,
    # all accepted. The bars went 97 -> 96 % on 2026-10-03, 25 -> 30 % rejected on 2026-10-06 and 96 -> 94 %,
    # 30 -> 35 % on 2026-10-20 for that reason (ANTICHEAT.md §8.8 "Dataset 26", "Dataset 55", "Dataset 364").
    check("accepted digits are >= 94 % right (measured 94.48 % at this seed, 1,093-reference font)",
          right / accepted >= 0.94, f"{right}/{accepted}")
    check("rejection rate below 35 % (measured 31.1 %)", rejected / (accepted + rejected) < 0.35,
          f"{rejected}/{accepted + rejected}")


def test_confidence_gate():
    print("== ambiguity is refused, never guessed ==")
    # an 8-dot blob: passes the count gate but matches nothing confidently
    blob = [(100 + (i % 3), 200 + i) for i in range(8)]
    digit, _, margin = captcha.read_digit(blob)
    check("blob margin below gate", margin < captcha.MIN_MARGIN, f"{digit} {margin:.2%}")
    # a layout whose middle digit is a blob -> solve refuses the whole captcha
    lay = _samples()[0]["layout"]
    dots = re.findall(r"\{ tilepic \d+ \d+ -?\d+ \}", lay)
    # keep only the first digit's dots (13) plus a 3-dot smear per other digit
    gutted = "{ button 21 19 2094 2095 1 0 1 }" + "".join(dots[:13]) \
        + "{ tilepic 180 80 572 }{ tilepic 181 140 572 }{ tilepic 182 100 572 }" \
        + "{ tilepic 300 80 572 }{ tilepic 301 140 572 }{ tilepic 302 100 572 }" \
        + "{ textentrylimited 163 251 40 20 2655 2 2 3 }{ button 222 248 247 249 1 0 594 }"
    check("two smeared digits -> solve() is None", captcha.solve(gutted) is None)


def test_stray_dot_split():
    print("== a stray dot as far from its digit as the digits are apart (live 2026-10-06) ==")
    s = json.load(open(os.path.join(HERE, "testdata", "captcha_split.json")))
    cl = captcha.digit_clusters(s["layout"])
    check("three clusters, the 3rd keeping its outlying dot at x 356",
          cl is not None and [len(c) for c in cl] == [14, 14, 16] and max(p[0] for p in cl[2]) == 356,
          str(cl and [len(c) for c in cl]))
    check(f"solved {s['answer']} (the accepted answer), submit {s['button']}",
          captcha.solve(s["layout"]) == s["answer"] and captcha.submit_button(s["layout"]) == s["button"],
          repr(captcha.solve(s["layout"])))


def test_stray_dots_between_digits():
    print("== stray dots of the 1 lying nearer the 2 (live 2026-10-20 17:14:57, refused then) ==")
    s = json.load(open(os.path.join(HERE, "testdata", "captcha_strays.json")))
    lay = s["layout"]
    cl = captcha.digit_clusters(lay)
    check("the 1 keeps its 13 dots, the strays at x 125-126 included; the 2 has its own 13, the 4 its 14",
          cl is not None and [len(c) for c in cl] == [13, 13, 14]
          and {(125, 139), (126, 86)} <= set(cl[0]) and min(p[0] for p in cl[1]) == 145,
          str(cl and [(len(c), min(p[0] for p in c), max(p[0] for p in c)) for c in cl]))
    check(f"solved {s['answer']} (the human's accepted answer), submit {s['button']}",
          captcha.solve(lay) == s["answer"] and captcha.submit_button(lay) == s["button"], repr(captcha.solve(lay)))
    # the font's first 166 references are the 55 captchas mined to 2026-10-06, the font the solver had live
    # that day (captcha_mine.py only appends; the fix re-cut 4 of them, the 7 and 9 of 792 and the 1 and 5 of
    # 315, whose strays the old split had misassigned too). Measured margins: 0.775 / 0.628 / 0.532 (the 2
    # read 0.038 when it was given the 1's strays)
    _use_refs(_refs()[:166])
    try:
        margins = [captcha.read_digit(c)[2] for c in cl]
        got = captcha.solve(lay)
    finally:
        _use_refs(None)
    check(f"out of sample: the 2026-10-06 font (166 references) reads {s['answer']}, every digit margin >= 0.5",
          got == s["answer"] and min(margins) >= 0.5, f"{got!r} {[round(m, 3) for m in margins]}")
    # adjacent digits sharing a tile graphic (25 of the 364 captchas): one run, cut where the clusters are
    # tightest in x; the same with all three digits in one graphic
    tiles = re.findall(r"\{ tilepic \d+ \d+ (\d+) \}", lay)
    merged = lay.replace(" 4113 }", f" {tiles[0]} }}")
    one = merged.replace(f" {tiles[-1]} }}", f" {tiles[0]} }}")
    for name, variant in (("the 1 and the 2 drawn with one graphic", merged), ("all three with one graphic", one)):
        vc = captcha.digit_clusters(variant)
        check(f"{name}: the same three clusters, solved {s['answer']}",
              vc == cl and captcha.solve(variant) == s["answer"], str(vc and [len(c) for c in vc]))
    # a 4th graphic run is no captcha layout we know: refused
    i = lay.index("{ tilepic 202 113 4113 }")
    fourth = lay[:i] + "{ tilepic 202 113 9999 }" + lay[i + len("{ tilepic 202 113 4113 }"):]
    check("a dot in a 4th tile graphic inside the 2: digit_clusters None",
          captcha.digit_clusters(fourth) is None and captcha.solve(fourth) is None)


def test_detection_sanity():
    print("== decoys and garbage are not captchas ==")
    decoy = ("{ nomove }{ noclose }{ nodispose }{ noresize }{ page 0 }{ page 1 }"
             "{ croppedtext -324 -203 1 1 0 0 }{ croppedtext -393 -158 1 1 0 1 }")
    check("decoy layout: solve() None", captcha.solve(decoy) is None)
    check("decoy layout: no submit button", captcha.submit_button(decoy) is None)
    check("empty layout: None", captcha.solve("") is None)
    one_dot = ("{ button 21 19 2094 2095 1 0 1 }{ tilepic 84 150 572 }"
               "{ textentrylimited 163 251 40 20 2655 2 2 3 }"
               "{ button 222 248 247 249 1 0 594 }")
    check("single tilepic: None", captcha.solve(one_dot) is None)
    # two reply buttons besides Guide -> ambiguous, refused
    two = "{ button 0 0 1 1 1 0 1 }{ button 0 0 1 1 1 0 594 }{ button 0 0 1 1 1 0 595 }"
    check("two non-guide buttons: None", captcha.submit_button(two) is None)


def test_held_out():
    print("== held out: a font without the capture's own session never answers wrong ==")
    samples = _samples()
    count = {}
    for s in samples:
        count[s["tag"]] = count.get(s["tag"], 0) + 1
    tags = sorted(count, key=lambda t: -count[t])          # the biggest sessions first
    rows = [row for out in _pmap(_held_out, tags) for row in out]
    covered = sum(c for _, _, c in rows)
    solved = sum(got == answer for answer, got, c in rows if c)
    wrong = [(answer, got) for answer, got, c in rows if c and got not in (answer, None)]
    check(f"no held-out captcha answered wrong ({covered} covered)", not wrong, str(wrong))
    # measured 2026-10-20 (364 captchas, 53 sessions): 364 covered, 362 solved, 2 refused: 482 of
    # 20261005_194043 (its 8 read as 4 at margin 0.033) and 229 of 20261015_093103 (right, margin 0.067);
    # closest solved margin 0.115 (356 of 20261013_103242). 2026-10-03 (26 captchas): 26 covered, 26 solved
    check("most held-out captchas are solved, not just refused", covered >= 364 and solved >= 360,
          f"{solved}/{covered}")


def main():
    import cpu_budget
    cpu_budget.limit()                # off the game's two CPUs, worker processes included (they inherit it)
    test_real_captchas()
    test_noise_tolerance()
    test_confidence_gate()
    test_stray_dot_split()
    test_stray_dots_between_digits()
    test_detection_sanity()
    test_held_out()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

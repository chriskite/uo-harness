"""Captcha solver tests (harness/captcha.py, ANTICHEAT.md §8.8/§8.13).

Ground truth: data/captcha_samples.json — every real captcha gump captured
through the proxy (sessions 20260929_204225 … 20261003_150103), each paired
with the answer the server accepted ("Captcha successful."). The reference
font (data/captcha_font.json) is derived from the same samples, so section 1
is in-sample; section 5 is the out-of-sample check.

Sections:
  1. End-to-end: every captured captcha solves to its recorded accepted answer,
     with its random submit button picked out (never the Guide button).
  2. Noise tolerance: jittered/dropped/noised variants of every reference
     digit classify correctly (the production setting: all references present).
  3. Confidence gate: ambiguous clusters are refused (None), not guessed.
  4. Detection sanity: decoy/garbage layouts are not captchas.
  5. Held out (leave one session out): each capture against a font without
     its own session's references is solved right or refused, never wrong,
     whenever every digit it needs still has a reference from another session.

Run: python harness/test_captcha.py
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import captcha

HERE = os.path.dirname(os.path.abspath(__file__))
FAILURES = 0


def check(name, cond, detail=""):
    global FAILURES
    if cond:
        print(f"  [OK] {name}")
    else:
        FAILURES += 1
        print(f"  [FAIL] {name}  {detail}")


def test_real_captchas():
    print("== every captured captcha solves to its accepted answer ==")
    samples = json.load(open(os.path.join(HERE, "data", "captcha_samples.json")))
    check("samples present", len(samples) == 24, str(len(samples)))
    for s in samples:
        got = captcha.solve(s["layout"])
        check(f"{s['tag']}: solved {s['answer']}", got == s["answer"], repr(got))
        btn = captcha.submit_button(s["layout"])
        check(f"{s['tag']}: submit button {s['button']} (random per captcha)",
              btn == s["button"] and btn != 1, str(btn))


def test_noise_tolerance():
    print("== jitter/dropout/noise tolerance (margin-gated) ==")
    rng = random.Random(23)
    font = captcha._font()
    accepted = right = rejected = 0
    for digit, ref in font:
        for _ in range(15):
            q = [(x + rng.gauss(0, 0.045), y + rng.gauss(0, 0.045))
                 for x, y in ref if rng.random() > 0.15]
            for _ in range(rng.randint(0, 2)):
                q.append((rng.uniform(-0.4, 0.4), rng.uniform(-0.5, 0.5)))
            pred, _, margin = captcha.read_digit(q)
            if margin < captcha.MIN_MARGIN:
                rejected += 1
                continue
            accepted += 1
            right += pred == digit
    # measured 2026-10-03 at this seed, 73-reference font: 97.2 % right, 19.4 % rejected (37-reference
    # font of 2026-10-01: 98.1 % / 16.4 %; 8 draws per reference read 96.1 %, too few for this bar)
    check("accepted digits are >= 97 % right (measured 97.2 % at this seed, 73-reference font)",
          right / accepted >= 0.97, f"{right}/{accepted}")
    check("rejection rate below 25 %", rejected / (accepted + rejected) < 0.25,
          f"{rejected}/{accepted + rejected}")


def test_confidence_gate():
    print("== ambiguity is refused, never guessed ==")
    # an 8-dot blob: passes the count gate but matches nothing confidently
    blob = [(100 + (i % 3), 200 + i) for i in range(8)]
    digit, _, margin = captcha.read_digit(blob)
    check("blob margin below gate", margin < captcha.MIN_MARGIN, f"{digit} {margin:.2%}")
    # a layout whose middle digit is a blob -> solve refuses the whole captcha
    samples = json.load(open(os.path.join(HERE, "data", "captcha_samples.json")))
    lay = samples[0]["layout"]
    import re
    dots = re.findall(r"\{ tilepic \d+ \d+ -?\d+ \}", lay)
    # keep only the first digit's dots (13) plus a 3-dot smear per other digit
    gutted = "{ button 21 19 2094 2095 1 0 1 }" + "".join(dots[:13]) \
        + "{ tilepic 180 80 572 }{ tilepic 181 140 572 }{ tilepic 182 100 572 }" \
        + "{ tilepic 300 80 572 }{ tilepic 301 140 572 }{ tilepic 302 100 572 }" \
        + "{ textentrylimited 163 251 40 20 2655 2 2 3 }{ button 222 248 247 249 1 0 594 }"
    check("two smeared digits -> solve() is None", captcha.solve(gutted) is None)


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
    samples = json.load(open(os.path.join(HERE, "data", "captcha_samples.json")))
    refs = json.load(open(os.path.join(HERE, "data", "captcha_font.json")))["references"]
    solved = covered = 0
    try:
        for s in samples:
            captcha._FONT = [(r["digit"], [tuple(p) for p in r["pts"]])
                             for r in refs if r["source"] != s["tag"]]
            if not set(s["answer"]) <= {d for d, _ in captcha._FONT}:
                continue  # a digit only this session showed (e.g. 9): nothing to generalize from
            covered += 1
            got = captcha.solve(s["layout"])
            check(f"{s['tag']} {s['answer']}: right or refused", got in (s["answer"], None), repr(got))
            solved += got == s["answer"]
    finally:
        captcha._FONT = None
    # measured 2026-10-03 (24 captchas): 24 covered, 24 solved; closest digit margin 0.247 (the 3 of
    # 373; 0.109 on 2026-10-01 when digit 3 had one reference from another session)
    check("most held-out captchas are solved, not just refused", covered >= 24 and solved >= 22,
          f"{solved}/{covered}")


def main():
    test_real_captchas()
    test_noise_tolerance()
    test_confidence_gate()
    test_detection_sanity()
    test_held_out()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

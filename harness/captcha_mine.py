"""Mine accepted captchas out of session captures into the captcha dataset.

    python harness/captcha_mine.py [--dry-run] [--samples PATH] [--font PATH] [TAG|BASE ...]

With no positional args every `logs/session_*.jsonl` capture is scanned; a
TAG (`20261003_150103`) or base path (`logs/session_<tag>`) limits the scan.
For each real captcha (S2C 0xB0/0xDD with gump id 1 and a `textentry`; decoys
have random ids and are never matched by text) the client's/agent's 0xB1
answer to that gump is paired with the server's "Captcha successful." speech
that follows it; only accepted answers become samples. Pairing for a gump
stops at the next captcha gump.

New captchas (layout not already in data/captcha_samples.json) are appended
to the samples, and their digit clusters, normalized, to the references of
data/captcha_font.json. Re-running is a no-op. For every new captcha the
report says whether the font as it was before the run would have solved it
and its weakest digit margin (the out-of-sample evidence). `--dry-run`
writes nothing; `--samples/--font` point at other copies of the two files.
After adding anything, update the hard-coded sample count and measured
figures in harness/test_captcha.py and rerun it (~5 min).
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import captcha
import replay
from world import parsers

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SAMPLES_PATH = os.path.join(HERE, "data", "captcha_samples.json")
SUCCESS = "Captcha successful."
ACCEPT_WINDOW = 10.0       # s from the 0xB1 answer to the success speech


def _is_captcha(g):
    return g is not None and g.get("gump_id") == 1 and "textentry" in g.get("layout", "")


def mine_capture(base):
    """[(sample, answer_time)] for every accepted captcha in capture `base`,
    plus a list of notes about captchas that were seen but not accepted."""
    tag = os.path.basename(base)[len("session_"):]
    packets = replay.timed_packets(base)
    found, notes = [], []
    gumps = []    # (index, gump) of every real captcha gump
    for i, (_, d, _, pkt) in enumerate(packets):
        if d == "s2c" and pkt[0] in (0xB0, 0xDD):
            g = parsers.parse_packet("s2c", pkt)
            if _is_captcha(g):
                gumps.append((i, g))
    for k, (i, g) in enumerate(gumps):
        end = gumps[k + 1][0] if k + 1 < len(gumps) else len(packets)
        answer = None    # (t, button_id, text) of the latest 0xB1 to this gump
        accepted = None
        for t, d, _, pkt in packets[i + 1:end]:
            if d == "c2s" and pkt[0] == 0xB1:
                r = parsers.parse_packet("c2s", pkt)
                if r["gump_id"] == 1 and r["serial"] == g["serial"]:
                    text = next((e["text"] for e in r["texts"] if e["text"].strip()), "")
                    answer = (t, r["button_id"], text.strip())
            elif d == "s2c" and pkt[0] in (0x1C, 0xAE) and answer is not None:
                sp = parsers.parse_packet("s2c", pkt)
                if sp and (sp.get("text") or "").strip() == SUCCESS \
                        and t - answer[0] <= ACCEPT_WINDOW:
                    accepted = answer
                    break
        t0 = packets[i][0]
        if accepted is None:
            notes.append(f"{tag} t={t0}: captcha not accepted in this capture"
                         + (f" (last answer {answer[2]!r})" if answer else " (no answer)"))
            continue
        found.append(({"tag": tag, "answer": accepted[2], "button": accepted[1],
                       "layout": g["layout"], "lines": g["lines"]}, t0))
    return found, notes


def _bases(args):
    if not args:
        return sorted(p[:-len(".jsonl")]
                      for p in glob.glob(os.path.join(ROOT, "logs", "session_*.jsonl")))
    out = []
    for a in args:
        if os.path.basename(a).startswith("session_"):
            out.append(a[:-len(".jsonl")] if a.endswith(".jsonl") else a)
        else:
            out.append(os.path.join(ROOT, "logs", f"session_{a}"))
    return out


def _load_exact(path, dump):
    """Parsed JSON of `path`, refusing files whose re-dump isn't byte-identical
    (the writer would otherwise silently reformat them)."""
    raw = open(path, encoding="utf-8").read()
    obj = json.loads(raw)
    if dump(obj) != raw:
        sys.exit(f"{path}: re-dump is not byte-identical; refusing to rewrite it")
    return obj


def _dump_samples(obj):
    return json.dumps(obj)


def _dump_font(obj):
    return json.dumps(obj, indent=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("captures", nargs="*", help="session tags or logs/session_<tag> bases")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--samples", default=SAMPLES_PATH)
    ap.add_argument("--font", default=captcha.FONT_PATH)
    a = ap.parse_args()

    samples = _load_exact(a.samples, _dump_samples)
    font = _load_exact(a.font, _dump_font)
    captcha.FONT_PATH, captcha._FONT = a.font, None    # judge against this font
    known = {s["layout"] for s in samples}

    accepted, present, skipped, new, refs, problems = 0, 0, 0, [], [], []
    for base in _bases(a.captures):
        if not all(os.path.exists(base + ext) for ext in (".jsonl", ".s2c.raw", ".c2s.raw")):
            problems.append(f"{base}: capture files missing")
            continue
        try:
            found, notes = mine_capture(base)
        except Exception as e:    # a damaged capture shouldn't stop the scan
            problems.append(f"{base}: not replayable, skipped ({type(e).__name__}: {e})")
            continue
        problems += notes
        for s, t0 in found:
            accepted += 1
            if s["layout"] in known:
                present += 1
                continue
            known.add(s["layout"])
            where = f"{s['tag']} t={t0} answer {s['answer']} button {s['button']}"
            clusters = captcha.digit_clusters(s["layout"])
            if clusters is None or len(s["answer"]) != 3 or not s["answer"].isdigit():
                skipped += 1
                problems.append(f"{where}: skipped, digit clusters unreadable or answer not 3 digits")
                continue
            if captcha.submit_button(s["layout"]) != s["button"]:
                skipped += 1
                problems.append(f"{where}: skipped, submit_button() = "
                                f"{captcha.submit_button(s['layout'])} != answered button")
                continue
            solved = captcha.solve(s["layout"])
            reads = [captcha.read_digit(cl) for cl in clusters]
            margin = min(m for _, _, m in reads)
            print(f"NEW {where}: current font -> {solved!r} "
                  f"({'right' if solved == s['answer'] else 'refused' if solved is None else 'WRONG'}), "
                  f"read {''.join(d for d, _, _ in reads)}, min margin {margin:.3f}")
            new.append(s)
            for digit, cl in zip(s["answer"], clusters):
                refs.append({"digit": digit, "source": s["tag"],
                             "pts": [[round(x, 4), round(y, 4)] for x, y in captcha._normalize(cl)]})

    for p in problems:
        print("note:", p)
    print(f"{accepted} accepted captchas found: {present} already present, "
          f"{len(new)} new, {skipped} skipped; "
          f"dataset {len(samples)} samples / {len(font['references'])} references")
    if not new:
        return
    if a.dry_run:
        print(f"dry run: would add {len(new)} samples / {len(refs)} references; nothing written")
        return
    samples += new
    font["references"] += refs
    with open(a.samples, "w", encoding="utf-8", newline="") as f:
        f.write(_dump_samples(samples))
    with open(a.font, "w", encoding="utf-8", newline="") as f:
        f.write(_dump_font(font))
    print(f"wrote {a.samples} ({len(samples)} samples) and {a.font} "
          f"({len(font['references'])} references)")
    print(f"REMINDER: harness/test_captcha.py hard-codes len(samples) == {len(samples) - len(new)} "
          f"and its measured figures; update them to the new dataset and rerun "
          f"`python harness/test_captcha.py` (~5 min).")


if __name__ == "__main__":
    main()

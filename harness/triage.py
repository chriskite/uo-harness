"""Speech triage with Laya (user request 2026-10-01; docs/PLAN.md "Laya speech triage").

Laya (github.com/NandhaKishorM/laya) answers typed questions over text in one
forward pass of an encoder; no text generation. Harvest jobs ask it about every
line a character says near us (speech_guard.py):

- `check`: is the speaker checking whether we're there, awake or a real person,
  asking what we're doing, or telling us to say or do something? At
  ESCALATE_CHECK or above, the line counts as a staff hint ("attendance check"),
  so the runner raises `gm_suspected` and the staff alarm at once instead of
  waiting for the overseer to read the line.
- `direct`: is it a greeting, question or request aimed at one person? Recorded
  only (shadow), never acted on.

Laya never lets the harness ignore speech: every line still holds the job for
the overseer (user decision 2026-10-01, "shadow + escalate"). Neither the
stock checkpoint nor our fine-tune can tell a GM's check from player chatter
safely (docs/PLAN.md has the numbers). Since v2 the service runs a checkpoint
fine-tuned on our two questions (triage_data.py, triage_train.py): it raises
the staff hint on more GM-style lines; it serves escalation only. Each verdict
is stored with its prompt (`state`) in the speech_nearby juncture and in the
`speech_clear` job event, next to how the hold ended: labeled data for the
next fine-tune.

The model runs in its own process: `laya-serve` from `.venv-laya` (torch is
not a harness dependency), on the GPU next to the client by default (fp16;
`--device cpu` is the fallback), on 127.0.0.1:25970 (25940-25960 is the
proxy's upstream bind range).
When it isn't running, verdicts carry an `error` and the hold works as before.

  python harness/triage.py serve [--device cpu]  # long-lived; listens ~10 s after start (cached checkpoint)
  python harness/triage.py serve --checkpoint stock    # the stock hub checkpoint instead of CHECKPOINT
  python harness/triage.py serve --checkpoint DIR --port 25971   # a candidate next to the live one (eval)
  python harness/triage.py judge "are you there?" [--speaker Kemp] [--me TestWorth]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VENV_SERVE = os.path.join(ROOT, ".venv-laya", "Scripts", "laya-serve.exe")
VENV_PY = os.path.join(ROOT, ".venv-laya", "Scripts", "python.exe")
STOCK = "stock"
# The stock checkpoint's hub commit (laya's own reviewed pin, revisions.PINNED_REVISIONS). Unpinned,
# laya-serve takes the hub's newest: on 2026-10-04 that had moved to 7b928d8, which nothing here measured.
STOCK_REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
# The fine-tuned checkpoint (triage_train.py run 4, 2026-10-04; docs/PLAN.md "Laya speech triage"),
# served by laya_serve_local.py. Not in git (models/ is ignored): rebuild it per docs/NOTES.md.
# Way back: CHECKPOINT = STOCK, or `triage.py serve --checkpoint stock` for one start.
CHECKPOINT = os.path.join(ROOT, "models", "laya-triage")
HOST, PORT = "127.0.0.1", 25970
DEFAULT_URL = f"http://{HOST}:{PORT}"
MODEL = "english"            # best of english/multilingual on the eval set; also gets the Turkish ads
DEVICE = "cuda"              # see docs/NOTES.md "Laya speech triage" for the GPU vs CPU measurements
AMP = "fp16"                 # CUDA autocast: Laya's default bf16 moves scores up to 0.073, fp16 within 0.019 (its BENCHMARKS)
THREADS = 4                  # CPU only: torch intra-op threads, leaving the cores to the client and proxy
TIMEOUT_S = 5.0
BACKOFF_S = 60.0             # after a failure, skip calls this long (a refused localhost connect may cost ~2 s on Windows [INFERENCE])
ESCALATE_CHECK = 0.6         # eval_triage.py, fine-tuned v2: 10/25 GM-style lines caught; player lines above: 4/104 held-out, 1/22 frozen
HINT = "attendance check"    # speech_guard.STAFF_HINTS prefix
VERSION = "v2"               # bump when QUESTIONS, state_text or the checkpoint change: verdicts of different versions don't compare
NEAR_TILES = 18              # the client's view range
NEAR_MAX = 6
ACTIVITY = "chopping trees in the forest"

QUESTIONS = {
    "check": {"type": "noul", "instructions":
              "Is the last speaker checking whether I am present, awake or a real person, asking what I am doing, "
              "or telling me to say or do something?"},
    "direct": {"type": "noul", "instructions":
               "Is this message a greeting, question or request directed at one specific nearby person?"},
}


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def nearby(world: dict, names: dict | None = None) -> list[tuple[str, int]]:
    """(name, tiles) of the characters near us, nearest first: players by the
    speech_guard rules, not NPCs, pets or monsters. `names` (serial key ->
    name, SpeechGuard.names) covers mobiles the world has no name for yet."""
    import speech_guard
    me = world.get("self") or {}
    mx, my = me.get("x"), me.get("y")
    my_serial = me.get("serial")
    labels = world.get("labels") or {}
    out = []
    for key, mob in (world.get("mobiles") or {}).items():
        if my_serial is not None and _serial(key) == _serial(my_serial):
            continue
        if not speech_guard.is_character(mob, labels.get(key)):
            continue
        if mx is None or mob.get("x") is None:
            continue
        d = max(abs(mob["x"] - mx), abs(mob["y"] - my))
        if d <= NEAR_TILES:
            out.append((mob.get("name") or (names or {}).get(key) or labels.get(key) or "someone", d))
    return sorted(out, key=lambda p: p[1])[:NEAR_MAX]


def state_text(who: dict, world: dict, activity: str = ACTIVITY, names: dict | None = None) -> str:
    """The text Laya reads: who we are, who's near, the recent speech ending
    with `who`'s line (speech_guard puts it in who["context"])."""
    me = (world.get("self") or {}).get("name") or "me"
    near = ", ".join(f"{n} ({d} tiles away)" for n, d in nearby(world, names)) or "nobody"
    lines = who.get("context") or [{"name": who.get("label") or who.get("name") or who["serial"],
                                     "text": who["text"]}]
    convo = "\n".join(f"{ln['name']}: {ln['text']}" for ln in lines)
    return (f"I am {me}, {activity}. Other characters near me: {near}.\n"
            f"Recent speech near me (last line is new):\n{convo}")


class Triage:
    """Client of the local laya-serve. `url` empty = off (judge does nothing)."""

    def __init__(self, url: str = DEFAULT_URL, timeout: float = TIMEOUT_S, log=print, now=time.monotonic):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.log = log
        self.now = now
        self.down_until = None       # monotonic time until which calls are skipped
        self.down_logged = False

    def ask(self, state: str) -> dict:
        body = json.dumps({"state": state, "questions": QUESTIONS, "model": MODEL}).encode()
        req = urllib.request.Request(self.url + "/v1/systemone", body, {"content-type": "application/json"})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            res = json.loads(r.read())
            infer = r.headers.get("X-Inference-Time-Ms")
        a = res["answers"]
        return {"check": round(float(a["check"]["noul"]), 4), "direct": round(float(a["direct"]["noul"]), 4),
                "model": (res.get("routing") or {}).get("model"), "ms": round((time.perf_counter() - t0) * 1000),
                "infer_ms": None if infer is None else round(float(infer), 1)}

    def judge(self, who: dict, world: dict, activity: str = ACTIVITY, names: dict | None = None) -> dict | None:
        """Ask Laya about `who`'s line; store the verdict in who["triage"] and,
        at ESCALATE_CHECK or above, add the staff hint to who["evidence"].
        None when off. Never raises: a failure is a verdict with `error`."""
        if not self.url:
            return None
        state = state_text(who, world, activity, names)
        if self.down_until is not None and self.now() < self.down_until:
            v = {"error": "unavailable (backing off after a failure)"}
        else:
            try:
                v = self.ask(state)
                self.down_until, self.down_logged = None, False
            except (OSError, ValueError, KeyError, TypeError) as e:
                self.down_until = self.now() + BACKOFF_S
                v = {"error": f"{type(e).__name__}: {e}"[:200]}
                if not self.down_logged:
                    self.log(f"speech triage unavailable ({v['error']}); holds carry no Laya verdict")
                    self.down_logged = True
        v = {"v": VERSION, **v, "state": state}
        who["triage"] = v
        if v.get("check", 0.0) >= ESCALATE_CHECK:
            who.setdefault("evidence", []).append(f"{HINT} (laya {v['check']:.2f})")
        return v


def serve(device: str = DEVICE, threads: int = THREADS, checkpoint: str = CHECKPOINT, port: int = PORT) -> int:
    if not os.path.exists(VENV_SERVE):
        print(f"{VENV_SERVE} missing: create the venv (docs/NOTES.md, Laya speech triage)", file=sys.stderr)
        return 2
    env = dict(os.environ, LAYA_HOST=HOST, LAYA_PORT=str(port), LAYA_DEVICE=device, LAYA_PRELOAD="1",
               LAYA_MODELS=MODEL, LAYA_LOG_LEVEL="warning", HF_HUB_DISABLE_SYMLINKS_WARNING="1")
    if device == "cpu":
        env["LAYA_THREADS"] = str(threads)
    else:
        env["LAYA_CUDA_AMP"] = AMP
    cmd = [VENV_SERVE]
    env["LAYA_REVISION"] = STOCK_REVISION        # a local checkpoint directory ignores it
    if checkpoint != STOCK:
        if not os.path.isfile(os.path.join(checkpoint, "rl_agent_config.json")):
            print(f"{checkpoint} is not a Laya checkpoint (triage_train.py writes one; `--checkpoint {STOCK}` "
                  f"serves the stock one)", file=sys.stderr)
            return 2
        env["LAYA_TRIAGE_CHECKPOINT"] = checkpoint
        cmd = [VENV_PY, os.path.join(HERE, "laya_serve_local.py")]
    print(f"laya-serve on http://{HOST}:{port} ({device}, {MODEL} = {checkpoint}); Ctrl+C to stop. A GPU it can't "
          f"use falls back to CPU silently: `triage.py health` shows where it runs", flush=True)
    try:
        return subprocess.call(cmd, env=env)
    except KeyboardInterrupt:
        return 0


def health(url: str = DEFAULT_URL) -> dict:
    with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=TIMEOUT_S) as r:
        return json.loads(r.read())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run laya-serve (long-lived)")
    s.add_argument("--device", choices=("cuda", "cpu"), default=DEVICE)
    s.add_argument("--threads", type=int, default=THREADS, help="CPU only")
    s.add_argument("--checkpoint", default=CHECKPOINT,
                   help=f"checkpoint directory, or '{STOCK}' for the stock hub checkpoint (default: CHECKPOINT)")
    s.add_argument("--port", type=int, default=PORT, help="another port runs a second instance (evaluation)")
    j = sub.add_parser("judge", help="one line through the running service; prints the verdict")
    j.add_argument("text")
    j.add_argument("--speaker", default="Someone")
    j.add_argument("--me", default="me")
    j.add_argument("--url", default=DEFAULT_URL)
    hp = sub.add_parser("health", help="the running service's /health (device each checkpoint runs on)")
    hp.add_argument("--url", default=DEFAULT_URL)
    a = ap.parse_args(argv)
    if a.cmd == "serve":
        return serve(a.device, a.threads, a.checkpoint, a.port)
    if a.cmd == "health":
        print(json.dumps(health(a.url), indent=1))
        return 0
    who = {"serial": "0x00000000", "text": a.text, "evidence": [],
           "context": [{"name": a.speaker, "text": a.text}]}
    v = Triage(a.url, log=lambda m: print(m, file=sys.stderr)).judge(who, {"self": {"name": a.me}})
    print(json.dumps({**v, "evidence": who["evidence"]}, ensure_ascii=False, indent=1))
    return 1 if "error" in v else 0


if __name__ == "__main__":
    sys.exit(main())

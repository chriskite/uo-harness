"""laya-serve with its `english` checkpoint loaded from a local directory: a
fine-tuned triage checkpoint (triage_train.py). Runs in .venv-laya; started by
`python harness/triage.py serve`, which sets the same LAYA_* environment as for
the stock laya-serve plus LAYA_TRIAGE_CHECKPOINT (the directory).

laya.serve reads no checkpoint path from the environment; its Router resolves
`english` through laya.router.DEFAULT_MODELS, which a local path may replace
(Router accepts a directory wherever a hub id goes). Requests keep sending
`model: english`, so clients don't change. /health reports the revision as
null for a local directory.
"""
import os
import sys

from laya import router, serve


def main() -> int:
    ckpt = os.environ.get("LAYA_TRIAGE_CHECKPOINT", "")
    if not os.path.isfile(os.path.join(ckpt, "rl_agent_config.json")):
        print(f"LAYA_TRIAGE_CHECKPOINT={ckpt!r} is not a Laya checkpoint directory", file=sys.stderr)
        return 2
    router.DEFAULT_MODELS["english"] = (os.path.abspath(ckpt), None)
    serve.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())

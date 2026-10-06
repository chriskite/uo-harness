"""Junctures per local day, stacked by kind (harness.db `junctures`, read-only snapshot)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save  # noqa: E402
from svgchart import bar  # noqa: E402

d = load("09_junctures")["by_day_kind"]
days = sorted(d)
kinds = ["threat", "task_failed", "speech_nearby", "task_done", "pk_escape", "captcha", "gm_suspected", "other"]
series = {k: [d[day][k] for day in days] for k in kinds}
series = {{"speech_nearby": "speech", "gm_suspected": "gm_susp."}.get(k, k): v for k, v in series.items()}
labels = [day[5:] for day in days]  # MM-DD
save("09_junctures", bar(labels, series, stacked=True, ylabel="junctures per day", xlabel="local day (2026)", h=340))

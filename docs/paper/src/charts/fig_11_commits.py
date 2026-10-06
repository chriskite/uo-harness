"""Commits per day and commits by hour of day (CDT), as a side-by-side pair.

Writes figures/11_commits_day.svg and figures/11_commits_hour.svg from data/11_commits.json.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save  # noqa: E402
from svgchart import bar  # noqa: E402

c = load("11_commits")
days = c["by_day"]
save("11_commits_day", bar([r["date"][5:] for r in days], {"commits": [r["commits"] for r in days]},
                           ylabel="commits per day", xlabel="day (2026)", values=True, w=440, h=300, ymax=100))
hours = c["by_hour"]
save("11_commits_hour", bar([f'{r["hour"]:02d}' for r in hours], {"commits": [r["commits"] for r in hours]},
                            ylabel="commits (all days)", xlabel="hour of day (CDT)", every=3, w=440, h=300,
                            ymax=40))

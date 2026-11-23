"""The Hunting Grounds simulator's calibration (harness/data/sim/hunting_grounds.json; docs/SIM.md "Hunting Grounds
simulator"): every number measured on Logan Wolf's runs of 2026-11-22, with [INFERENCE] marking the guesses there."""
from __future__ import annotations

import copy
import functools
import json
import os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sim", "hunting_grounds.json")


@functools.lru_cache(maxsize=None)
def _read(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load(path: str | None = None) -> dict:
    """A fresh copy of the scenario (callers may change it: a test's tweak stays its own)."""
    return copy.deepcopy(_read(path or PATH))

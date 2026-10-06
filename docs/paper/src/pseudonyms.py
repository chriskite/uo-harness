"""Pseudonymization rules for the paper's public edition (rules in pseudonyms.json).

Shared by build.py (prose, captions, mermaid, byline) and charts/common.py (chart text, applied
before matplotlib lays the figure out, so longer pseudonyms still fit). The chart scripts find the
rules through the PAPER_PSEUDONYMS environment variable, which build.py sets for that render only.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

CONFIG = Path(os.path.dirname(os.path.abspath(__file__))) / "pseudonyms.json"
ENV = "PAPER_PSEUDONYMS"


class Rules:
    def __init__(self, path: Path = CONFIG):
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        self.path = Path(path)
        self.output = cfg["output"]
        self.notice = cfg.get("notice", "")
        self.replace = [(re.compile(p), r) for p, r in cfg["replace"]]
        self.forbid = [re.compile(p, re.I) for p in cfg["forbid"]]
        self.withheld = set(cfg.get("withhold", []))

    def withholds(self, label: str) -> bool:
        """True for chart elements (by their original label) the edition leaves out."""
        return label in self.withheld

    def apply(self, text: str) -> str:
        for pat, rep in self.replace:
            text = pat.sub(rep, text)
        return text

    def leaks(self, text: str, context: int = 40) -> list[str]:
        """Every forbidden match left in text, with a little context."""
        found = []
        for pat in self.forbid:
            for m in pat.finditer(text):
                a, b = max(0, m.start() - context), min(len(text), m.end() + context)
                found.append(f"{pat.pattern!r}: …{text[a:b]!r}…")
        return found


def from_env() -> Rules | None:
    """The rules if this process renders the pseudonymized edition, else None."""
    p = os.environ.get(ENV)
    return Rules(Path(p)) if p else None

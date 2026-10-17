"""Read-only access to the stock client's per-character settings:
`ClassicUO/Data/Profiles/<account>/<server>/<character>/profile.json` under the install dir.

Only read (the install dir is off-limits for writes, AGENTS.md constraint 1). The account is
the 0x91 login name (world state `self.account`), the character the 0x5D name.
"""
import glob
import json
import os

import uomap

PROFILES = os.path.join(uomap.INSTALL, "ClassicUO", "Data", "Profiles")


def speech_hue(account: str, name: str, root: str | None = None) -> int | None:
    """The character's ClassicUO `speech_hue` (the hue its typed speech goes out in), or None
    when there's no readable profile. Several server folders: the newest profile wins.
    `root` defaults to PROFILES."""
    paths = glob.glob(os.path.join(glob.escape(root or PROFILES), glob.escape(account), "*",
                                   glob.escape(name), "profile.json"))
    for path in sorted(paths, key=os.path.getmtime, reverse=True):
        try:
            with open(path, encoding="utf-8-sig") as f:
                hue = json.load(f).get("speech_hue")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(hue, int) and 0 < hue <= 0xFFFF:
            return hue
    return None

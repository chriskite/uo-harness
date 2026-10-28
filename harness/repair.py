"""The house's repair bench: one double-click repairs every worn item and every item in the backpack (armor,
weapons, instruments: their uses back to full), spending the bench's charges (user 2026-10-27: "double click the
repair bench before each trip into a dungeon"; KB 'repair benches': no skill needed, recharged with repair kits).

Live (capture 20261027_220057, Logan Wolf: the public bench 0x40C3A1BD at the DTF guild house, then the house's own
0x5B3BF0CF one tile east of the home landing) the double-click (0x06) answers with one system line:
  - "Repaired 1 item." (22:03:16.9) / "Repaired 7 items." (23:03:00.5): 2.1 s after the click, the bench's hammer
    (sound 42) once a second on the way.
  - "That repair bench is out of charges (use any repair kit on the bench to add additional charges)." 0.05 s
    after it (23:08:12.7, the new bench before its kits).
  - "You do not have any items equipped or in your backpack in need of repairs." 0.04 s after it (23:15:24.3).
A repair kit's cursor on the bench adds its uses: "You add repair kits into the repair bench (200/5000 uses
remaining)." (capture 20261027_230843, 23:15:15.7).

IO-agnostic like dropoff.py: `io` has send(pkt) and poll() -> (state, new world events) (escape.LinkIO for
runners, ctl._CtlIO for ctl)."""

from __future__ import annotations

import re
import time

import actions
from agent_link import Deadline

RANGE = 2                     # the bench stands 1 tile off the home landing (3576,3251 by 3575,3251)
WAIT_S = 8.0                  # the click's line (live 2.1 s for a repair, 0.05 s otherwise)
RECLICK_S = 0.6               # no hammer and no line this long after the click: click once more (escape._open)
HAMMER_SOUND = 42             # 0x54 at the bench while it works

_REPAIRED = re.compile(r"^Repaired (\d+) items?\.$")
_NOTHING = "in need of repairs"
_EMPTY = "out of charges"


class RepairError(Exception):
    """The bench couldn't be used (the message says why)."""


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def _system_line(e: dict) -> str | None:
    if e.get("ev") == "speech_heard" and _serial(e.get("serial") or 0xFFFFFFFF) == 0xFFFFFFFF:
        return (e.get("text") or "").strip()
    return None


def run(io, human, bench: dict, *, timeout: float = WAIT_S) -> dict:
    """Double-click `bench` (homes.json `repair_bench`: {serial, pos}) from where we stand and read its line.
    Returns {ok, repaired (items; 0 when nothing needed it), empty (the bench is out of charges), line, heard}:
    ok is False only for an empty bench. Raises RepairError when the bench isn't in view within RANGE (nothing
    sent) or no known line comes in `timeout` (a world save doesn't use it up)."""
    serial = _serial(bench["serial"])
    st, _ = io.poll()
    it, pos = st["world"]["items"].get(f"0x{serial:08X}"), st["movement"].get("pos")
    if it is None or it.get("x") is None or not pos or max(abs(it["x"] - pos[0]), abs(it["y"] - pos[1])) > RANGE:
        raise RepairError(f"the repair bench 0x{serial:08X} isn't in view within {RANGE} tiles: "
                          "stand on the home landing tile")
    human.wait("use")
    io.send(actions.dclick(serial))
    t0, heard, started, reclicked = time.monotonic(), [], False, False
    deadline = Deadline(timeout, getattr(io, "save_t0", None))
    while not deadline.over():
        _, evs = io.poll()
        deadline.see(evs)
        for e in evs:
            if e.get("ev") == "sound" and e.get("sound") == HAMMER_SOUND:
                started = True
            line = _system_line(e)
            if line is None:
                continue
            heard.append(line)
            if m := _REPAIRED.match(line):
                return {"ok": True, "repaired": int(m.group(1)), "empty": False, "line": line, "heard": heard}
            if _NOTHING in line:
                return {"ok": True, "repaired": 0, "empty": False, "line": line, "heard": heard}
            if _EMPTY in line:
                return {"ok": False, "repaired": 0, "empty": True, "line": line, "heard": heard}
        if not started and not heard and not reclicked and not deadline.saving \
                and time.monotonic() - t0 > RECLICK_S:
            io.send(actions.dclick(serial))       # a double-click right after another action is ignored
            reclicked = True
        time.sleep(0.05)
    raise RepairError(f"no answer from the repair bench 0x{serial:08X} in {timeout:g} s"
                      + (f" (heard: {' / '.join(heard)})" if heard else ""))

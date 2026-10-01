"""Sounds for the human at the PC (PLAN.md Phase 4: handoff alert = sound).

- handoff(): three even beeps. Signals a captcha or a speech hold (captcha
  auto-solve is pending; until then the beeps call for the solve).
- staff(): a distinct two-tone alarm. Someone who may be staff (a GM) is near
  the character: the human should come and check (user request 2026-10-01).
  It repeats every STAFF_REPEAT_S while a `gm_suspected` juncture is open:
  staff_alarm_due() is called by a holding runner and by `ctl wait`, and the
  last sound time is shared through the store's meta table, so the two don't
  double up. Acking the juncture (`ctl ack <id>`) stops it.
"""
from __future__ import annotations

import os
import time

import task_wrap as tw

STAFF_REPEAT_S = 30.0
GM_KIND = "gm_suspected"
LAST_STAFF_KEY = "gm_alarm_t"
QUIET_ENV = "UO_QUIET"          # tests: no sound from any process


def _beep(tones):
    if os.environ.get(QUIET_ENV):
        return
    try:
        import winsound
        for hz, ms in tones:
            winsound.Beep(hz, ms)
    except (ImportError, RuntimeError):
        print("\a", end="", flush=True)


def handoff(sound: bool = True):
    if sound:
        _beep([(880, 250)] * 3)


def staff(sound: bool = True):
    if sound:
        _beep([(1320, 180), (660, 180)] * 4)


def open_gm(mem) -> list[int]:
    """Ids of open gm_suspected junctures."""
    return [r[0] for r in mem.con.execute(
        "SELECT id FROM junctures WHERE kind=? AND acked_t IS NULL ORDER BY id", (GM_KIND,))]


def staff_alarm_due(mem, sound: bool = True, now: float | None = None) -> bool:
    """Sound the staff alarm if a gm_suspected juncture is open and nobody
    sounded it in the last STAFF_REPEAT_S. True when it sounded."""
    if not open_gm(mem):
        return False
    now = time.time() if now is None else now
    last = float(tw.meta_get(mem, LAST_STAFF_KEY, "0") or 0)
    if now - last < STAFF_REPEAT_S:
        return False
    tw.meta_set(mem, LAST_STAFF_KEY, f"{now:.2f}")
    staff(sound)
    return True


def post_gm(mem, source: str, reason: str, data: dict, sound: bool = True) -> int:
    """Post a gm_suspected juncture (urgent) and sound the staff alarm now."""
    jid = mem.juncture(source, GM_KIND, f"Possible staff (GM) nearby: {reason}"[:300], "urgent", data)
    tw.meta_set(mem, LAST_STAFF_KEY, f"{time.time():.2f}")
    staff(sound)
    return jid

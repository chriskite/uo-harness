"""Sounds for the human at the PC (PLAN.md Phase 4: handoff alert = sound).

- handoff(): three even beeps. Signals a captcha for the human to solve
  (captcha mode "human", or "auto" couldn't read it) or a speech hold.
- staff(): a distinct two-tone alarm. Someone who may be staff (a GM) is near
  the character: the human should come and check (user request 2026-10-01).
  It repeats every STAFF_REPEAT_S while a `gm_suspected` juncture is open:
  staff_alarm_due() is called by a holding runner and by `ctl wait`, and the
  last sound time is shared through the store's meta table, so the two don't
  double up. Acking the juncture (`ctl ack <id>`) stops it.
- eerie(): a ghostly wail (a theremin-like glide with vibrato and a tritone
  shadow, synthesized as a WAV). An eerie corpse of the Field of Souls event came
  into view (user request 2026-10-17; the proxy sounds it once per corpse,
  soul_jar.EerieSeen).
- spirit(): a rising bell chime (A minor arpeggio, bell partials), so it can't be
  taken for the wail. Our name-match eerie spirit came into view in the Field of
  Souls (user request 2026-10-17; the proxy, soul_jar.SpiritWatch).
  Both synthesized sounds return at once: they play on their own thread, so the
  proxy's relay never waits for them, one at a time.
"""
from __future__ import annotations

import io
import math
import os
import struct
import threading
import time
import wave

import task_wrap as tw

STAFF_REPEAT_S = 30.0
GM_KIND = "gm_suspected"
LAST_STAFF_KEY = "gm_alarm_t"
QUIET_ENV = "UO_QUIET"          # tests: no sound from any process
# eerie(): the wail. Legs (seconds, from Hz, to Hz), glided exponentially: up, a held wobble, a long fall
RATE = 22050
EERIE_LEGS = ((0.45, 330.0, 494.0), (0.35, 494.0, 494.0), (1.2, 494.0, 196.0))
EERIE_VIBRATO = (5.5, 0.025)     # Hz, depth (share of the pitch)
EERIE_SHADOW = (math.sqrt(2.0), 0.3)   # a second voice a tritone up, at this share of the first's loudness
EERIE_ATTACK_S, EERIE_RELEASE_S = 0.12, 0.6
EERIE_LEVEL = 0.6                 # of full scale
# spirit(): the chime. Bells (start s, Hz) rising, each a fundamental plus an inharmonic partial, decaying
SPIRIT_BELLS = ((0.0, 880.0), (0.14, 1046.5), (0.28, 1318.5), (0.42, 1760.0))
SPIRIT_PARTIAL = (2.76, 0.4)      # the bell's partial: frequency ratio, loudness share
SPIRIT_DECAY_S = 0.35             # e-folding time of each bell
SPIRIT_LEN_S = 1.4
SPIRIT_LEVEL = 0.45

_wavs: dict[str, bytes] = {}
_sound_lock = threading.Lock()   # one synthesized sound at a time: a second waits for the first to finish


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


def _wav(samples) -> bytes:
    """A 16-bit mono WAV at RATE of `samples` (floats in -1..1)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767)) for v in samples))
    return buf.getvalue()


def _eerie_samples():
    total = sum(leg[0] for leg in EERIE_LEGS)
    (vib_hz, vib_depth), (ratio, share) = EERIE_VIBRATO, EERIE_SHADOW
    phase = phase2 = 0.0
    for i in range(int(total * RATE)):
        t = i / RATE
        start = 0.0
        for secs, f0, f1 in EERIE_LEGS:
            if t < start + secs or (secs, f0, f1) == EERIE_LEGS[-1]:
                hz = f0 * (f1 / f0) ** min(1.0, (t - start) / secs)
                break
            start += secs
        hz *= 1.0 + vib_depth * math.sin(2 * math.pi * vib_hz * t)
        phase += 2 * math.pi * hz / RATE
        phase2 += 2 * math.pi * hz * ratio / RATE
        env = min(1.0, t / EERIE_ATTACK_S, (total - t) / EERIE_RELEASE_S)
        yield EERIE_LEVEL * env * (math.sin(phase) + share * math.sin(phase2)) / (1.0 + share)


def _spirit_samples():
    ratio, share = SPIRIT_PARTIAL
    n = int(SPIRIT_LEN_S * RATE)
    for i in range(n):
        t, v = i / RATE, 0.0
        for start, hz in SPIRIT_BELLS:
            if t >= start:
                u = t - start
                v += math.exp(-u / SPIRIT_DECAY_S) * (math.sin(2 * math.pi * hz * u)
                                                      + share * math.sin(2 * math.pi * hz * ratio * u))
        fade = min(1.0, (SPIRIT_LEN_S - t) / 0.1)       # no click at the end
        yield SPIRIT_LEVEL * fade * v / (1.0 + share) / 2.0


_BUILDERS = {"eerie": _eerie_samples, "spirit": _spirit_samples}


def sound_wav(name: str) -> bytes:
    """The synthesized sound `name` ("eerie", "spirit") as WAV bytes, built on first use."""
    if name not in _wavs:
        _wavs[name] = _wav(_BUILDERS[name]())
    return _wavs[name]


def _play(name: str):
    with _sound_lock:
        try:
            import winsound
            winsound.PlaySound(sound_wav(name), winsound.SND_MEMORY)   # SND_MEMORY can't be async: this thread waits
        except (ImportError, RuntimeError):
            print("\a", end="", flush=True)


def _start(name: str, sound: bool) -> threading.Thread | None:
    if not sound or os.environ.get(QUIET_ENV):
        return None
    th = threading.Thread(target=_play, args=(name,), name=f"{name}-alarm", daemon=True)
    th.start()
    return th


def eerie(sound: bool = True) -> threading.Thread | None:
    """The eerie-corpse wail, on its own daemon thread (returned; None when quiet)."""
    return _start("eerie", sound)


def spirit(sound: bool = True) -> threading.Thread | None:
    """The name-match spirit chime, on its own daemon thread (returned; None when quiet)."""
    return _start("spirit", sound)


def open_gm(mem) -> list[int]:
    """Ids of open gm_suspected junctures (this character's and unscoped ones; all without one)."""
    scope, params = mem.scope_sql()
    return [r[0] for r in mem.con.execute(
        "SELECT id FROM junctures WHERE kind=? AND acked_t IS NULL" + scope + " ORDER BY id",
        (GM_KIND, *params))]


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

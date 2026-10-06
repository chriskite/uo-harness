"""Logs into boards as one deterministic flow: the lumber runner's convert in the rental room
(loop_lumber.convert) and the overseer's `ctl act convert` (docs/OVERSEER.md §2) run this code.

Every log stack in the backpack, at any bag depth, becomes boards with the hatchet: use the hatchet
(its target cursor), target the stack, the server answers "You shape the logs into boards."
(harness/data/loops/lumber.json `convert.ok_text`, the user's demonstration) and the boards land
where the logs were [INFERENCE: RunUO ScissorHelper drops them into the logs' container]. One
conversion per stack (logs of several woods, what aborted trips left: live 2026-10-05, 4 stacks)
plus CONVERT_RETRIES for hatchet uses that bring no cursor (one did right after the pouch went off,
and a 4-try loop left a 9-log stack; docs/NOTES.md "Convert left logs").

A trip carries its logs in a live trapped pouch (hue 38, harness/pouch.py), which a double-click
only sets off; the logs inside can't be targeted while it is closed. So each live pouch holding
logs is set off first by our own double-click (unpack: a hit, no alarm; it is an ordinary pouch
after that) and opened on the way to its stacks, like a player opening the bag.

IO-agnostic like shelf.py and stockpile.py: `io` has send(pkt) and poll() -> (state, new world
events) (escape.LinkIO for runners, ctl._CtlIO for ctl). What differs between the runner and ctl
comes in as callables: open_for(*needs) opens the containers the client must show first ((serial,
itself) pairs, agent_link.containers_to_open), use_hatchet() returns the hatchet's target-cursor
event or None (the runner's handles captchas and hesitation; use_tool is ctl's plain one), and the
hooks on_pop / on_stack / on_target tell the runner's pop watch, viz intent and ledger."""

from __future__ import annotations

import time

import actions
import combat
import ledger
import pouch

LOGS = ledger.LOG_GRAPHICS            # logs of every wood (ordinary 0x1BDD; colours by hue)
BOARDS = ledger.BOARD_GRAPHICS
CONVERT_RETRIES = 3                   # hatchet uses without a cursor tolerated (one live, 2026-10-05)
POP_WAIT_S = 4.0                      # our double-click -> the pouch re-sent with hue 0
CURSOR_WAIT_S = 4.0                   # the hatchet's double-click -> its target cursor
CONVERT_WAIT_S = 5.0                  # the target -> "You shape the logs into boards."
SETTLE_S = 1.0                        # the ok text -> the log stack gone from the world model
POLL_S = 0.1


class ConvertError(Exception):
    """The conversion couldn't go on (no backpack, a captcha up for ctl, a proxy refusal)."""


def _key(serial: int) -> str:
    return f"0x{serial:08X}"


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def backpack(st: dict) -> int:
    pack = combat.backpack(st["world"]["items"], st["movement"].get("self_serial"))
    if pack is None:
        raise ConvertError("backpack not known to the world model")
    return pack


def stacks(st: dict, graphics=LOGS) -> list[tuple[int, dict]]:
    """[(serial, item)] of `graphics` in the backpack at any bag depth, by serial."""
    items = st["world"]["items"]
    inside = {k for k, _ in combat.pack_items(items, backpack(st))}
    return sorted((_serial(k), it) for k, it in items.items() if k in inside and it.get("graphic") in graphics)


def count(st: dict, graphics) -> int:
    return sum(it.get("amount") or 1 for _, it in stacks(st, graphics))


def _wait(io, done, timeout: float):
    """Poll until done(state, events seen so far) holds: (state, events), or (None, events) at the timeout."""
    end, got = time.monotonic() + timeout, []
    while True:
        st, evs = io.poll()
        got += evs
        if done(st, got):
            return st, got
        if time.monotonic() > end:
            return None, got
        time.sleep(POLL_S)


def _quiet(_msg: str):
    pass


def unpack(io, human, graphics, *, open_for, on_pop=None, log=_quiet) -> list[int]:
    """Set off, ourselves, every live trapped pouch holding `graphics`, so that the next
    double-click opens it (a double-click on a live one only sets it off; then it is an ordinary
    pouch, hue 0). Our pop costs a hit and is no alarm (on_pop(p) before the click: the runner's
    pouch.PopWatch and HP guard). Returns the pouches set off; ConvertError when one doesn't go off."""
    st, _ = io.poll()
    done = []
    for p in pouch.holding(st["world"], backpack(st), graphics):
        open_for((p, False))
        human.wait("use")
        if on_pop is not None:
            on_pop(p)
        io.poll()
        io.send(actions.dclick(p))
        if _wait(io, lambda s, _e: (s["world"]["items"].get(_key(p)) or {}).get("hue") != pouch.TRAPPED_HUE,
                 POP_WAIT_S)[0] is None:
            raise ConvertError(f"trapped pouch {_key(p)} didn't go off on our double-click")
        log(f"set off our trapped pouch {_key(p)} to open it")
        done.append(p)
        human.wait("read")
    return done


def use_tool(io, human, tool: int, *, open_for, captcha_gump_id: int | None = None):
    """Double-click `tool` (bags on the way opened first) after a `use` pause; its target-cursor
    event, or None when none came within CURSOR_WAIT_S. A captcha gump (captcha_gump_id) raises
    ConvertError: only the runner answers captchas."""
    human.wait("use")
    open_for((tool, False))
    io.poll()
    io.send(actions.dclick(tool))
    _, got = _wait(io, lambda _s, evs: any(
        e.get("ev") == "target" or (captcha_gump_id is not None and e.get("ev") == "gump_open"
                                    and _serial(e.get("gump_id") or 0) == captcha_gump_id) for e in evs),
        CURSOR_WAIT_S)
    if captcha_gump_id is not None and any(e.get("ev") == "gump_open" and _serial(e.get("gump_id") or 0)
                                           == captcha_gump_id for e in got):
        raise ConvertError("a captcha came up: answer it in the client, then convert again")
    return next((e for e in reversed(got) if e.get("ev") == "target"), None)


def convert(io, human, *, use_hatchet, open_for, ok_text: str, on_pop=None, on_stack=None, on_target=None,
            log=_quiet) -> dict:
    """Every log stack in the pack into boards (module docstring): the live trapped pouches holding
    logs set off first (unpack), then per stack open_for its containers, use_hatchet(), an aim pause,
    on_target(serial) (the runner's ledger: the stack is consumed, not stolen), the target, and the
    ok text. on_stack(serial, item) comes before each try (the runner's viz intent).

    Returns {ok, converted [{serial, graphic, hue, logs, boards}], boards (added in all), logs_left,
    pouches (set off), tries, no_cursor (hatchet uses without a cursor), error?}. A stack the server
    doesn't convert ends it (error, the rest left); so do more tries than stacks + CONVERT_RETRIES,
    and a ConvertError (a pouch that doesn't go off, a captcha for ctl, a proxy refusal). What the
    callables raise otherwise (the runner's Abort and Escape) goes through."""
    out = {"ok": False, "converted": [], "boards": 0, "logs_left": 0, "pouches": [], "tries": 0, "no_cursor": 0}
    try:
        error = _convert(io, human, out, use_hatchet, open_for, ok_text, on_pop, on_stack, on_target, log)
    except ConvertError as e:
        error = str(e)
    st, _ = io.poll()
    out["logs_left"] = count(st, LOGS)
    out["ok"] = error is None
    if error is not None:
        out["error"] = error
    return out


def _convert(io, human, out, use_hatchet, open_for, ok_text, on_pop, on_stack, on_target, log) -> str | None:
    """convert's flow, filling `out`; the error that ended it, or None when no logs are left."""
    out["pouches"] = [_key(p) for p in unpack(io, human, LOGS, open_for=open_for, on_pop=on_pop, log=log)]
    st, _ = io.poll()
    tries = len(stacks(st)) + CONVERT_RETRIES
    for _ in range(tries):
        st, _ = io.poll()
        logs = stacks(st)
        if not logs:
            return None
        serial, it = logs[0]
        amount = it.get("amount") or 1
        out["tries"] += 1
        if on_stack is not None:
            on_stack(serial, it)
        open_for((serial, False))                # the logs are targeted in their open container
        cur = use_hatchet()
        if cur is None:
            out["no_cursor"] += 1
            log("convert: the hatchet brought no cursor; trying again")
            continue
        human.wait("aim")
        st, _ = io.poll()
        before = count(st, BOARDS)
        if on_target is not None:
            on_target(serial)
        io.send(actions.target_object(cur["cursor_id"], serial, it.get("x") or 0, it.get("y") or 0, 0,
                                      it["graphic"], cursor_type=cur.get("cursor_type") or 0))
        st, _ = _wait(io, lambda _s, evs: any(e.get("ev") == "speech_heard" and e.get("text") == ok_text
                                              for e in evs), CONVERT_WAIT_S)
        if st is None:
            return f"log stack {_key(serial)} did not convert"
        st, _ = _wait(io, lambda s, _e: _key(serial) not in s["world"]["items"] and count(s, BOARDS) > before,
                      SETTLE_S)
        st = st or io.poll()[0]
        boards = max(0, count(st, BOARDS) - before)
        out["converted"].append({"serial": _key(serial), "graphic": f"0x{it['graphic']:04X}", "hue": it.get("hue"),
                                 "logs": amount, "boards": boards})
        out["boards"] += boards
        log(f"converted {amount} logs to boards")
        human.wait("between")
    st, _ = io.poll()
    return None if not stacks(st) else f"logs left after {tries} conversion tries"

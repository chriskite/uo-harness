"""Our mount: find it, ride it, and know when it's a ghost (live 2026-10-05, Outland Dan's bonded
horse; docs/NOTES.md "Our mount").

- Riding is an item on the mount layer (0x19) on us (Dan's horse: "deck" 0x4816EF0E). Off the
  horse, the horse is a mobile that follows us (Dan's 0x0154FE11), our follower count goes
  0/5 -> 1/5.
- Mounting is the stock double-click on the horse; dismounting the double-click on ourselves.
- Our own pet's context menu offers the pet commands (live: Animal Lore, Kill, Patrol, Guard,
  Follow, Come, Move, Stay, Stop, Release, Transfer); "Release" is how we know a pet is ours.
- A bonded pet that dies becomes a ghost that follows us (S2C 0xBF sub 0x19 dead = 1, hits 0;
  world `mobiles[s].dead`). Live 2026-10-05 the ghost followed Dan through a recall home and came
  back alive (10/100 hits) when he entered the rental room through the house steward
  [mechanism unknown, one observation]; healers and stable masters also revive pets (wiki).
- A recall into the DTF guild house sends a ridden mount to rest ("Your mount finds a quiet place
  to rest safely.": the mount item goes, followers stay 0/5); going into the rental room gives it
  back ("Your mount returns.", live 2026-10-05). Leaving the room keeps it under us.

IO-agnostic like room.py: `io` has send(pkt) and poll() -> (state, new world events)."""

from __future__ import annotations

import time

import actions
import room
from agent_link import cheb

LAYER_MOUNT = 0x19
PET_RANGE = 3                       # tiles: a following pet stays this close
OWN_MENU_ENTRY = "release"          # only the owner's context menu on a pet offers it (live)
MENU_WAIT_S = 3.0
MOUNT_WAIT_S = 3.0
HUMAN_BODIES = frozenset([*range(0x190, 0x194), *range(0xB7, 0xBB), *range(0x25D, 0x261), 0x29A, 0x29B,
                          0x2B6, 0x2B7, 0x3DB, 0x3DF, 0x3E2, 0x2E8, 0x2E9, 0x4E5])
OWN_MOUNTS_KEY = "own_mounts"       # memory-store meta: {character: {serial, name, t}}


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def remembered(mem, character: str | None) -> int | None:
    """The pet serial remembered as `character`'s mount (memory-store meta `own_mounts`), else None."""
    import json
    import task_wrap
    got = json.loads(task_wrap.meta_get(mem, OWN_MOUNTS_KEY) or "{}").get((character or "").strip().lower())
    return _serial(got["serial"]) if got else None


def remember(mem, character: str | None, serial: int, name: str | None):
    """Keep `serial` as `character`'s mount (its menu said it's ours)."""
    import json
    import task_wrap
    if not character:
        return
    pets = json.loads(task_wrap.meta_get(mem, OWN_MOUNTS_KEY) or "{}")
    pets[character.strip().lower()] = {"serial": f"0x{serial:08X}", "name": name, "t": round(time.time(), 1)}
    task_wrap.meta_set(mem, OWN_MOUNTS_KEY, json.dumps(pets))


def mounted(state: dict) -> bool:
    me = state["movement"].get("self_serial")
    return me is not None and any(
        it.get("layer") == LAYER_MOUNT and it.get("container") is not None and _serial(it["container"]) == me
        for it in (state["world"].get("items") or {}).values())


def pets_near(state: dict, rng: int = PET_RANGE, also: int | None = None) -> list[tuple[int, int, dict]]:
    """[(distance, serial, mobile)] of the pets within `rng` tiles, nearest first: a "(bonded)" /
    "(tame)" line seen (world `pet`), a ghost pet flag, or the serial `also` (a mount remembered
    as ours: its tag line comes only when the client asks); never a human body or ourselves."""
    pos = state["movement"].get("pos")
    me = state["movement"].get("self_serial")
    out = []
    for key, m in (state["world"].get("mobiles") or {}).items():
        s = _serial(key)
        if s == me or m.get("x") is None or not pos or m.get("graphic") in HUMAN_BODIES:
            continue
        if m.get("pet") not in ("bonded", "tame") and not m.get("dead") and s != also:
            continue
        d = cheb((m["x"], m["y"]), tuple(pos[:2]))
        if d <= rng:
            out.append((d, s, m))
    return sorted(out, key=lambda t: t[:2])


def is_own(io, human, serial: int, timeout: float = MENU_WAIT_S) -> bool:
    """The stock right-click on the pet: our own offers the pet commands ("Release"). The menu is
    left to the client (closing it sends nothing, as for a player who clicks away)."""
    io.poll()
    human.wait("use")
    io.send(actions.single_click(serial))
    io.send(actions.request_popup(serial))
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _, evs = io.poll()
        for e in evs:
            if e.get("ev") == "popup" and e.get("serial") is not None and _serial(e["serial"]) == serial:
                texts = [(x.get("text") or room._cliloc(x["cliloc"]) or "").strip().lower()
                         for x in e.get("entries") or []]
                return OWN_MENU_ENTRY in texts
        time.sleep(0.05)
    return False


def find_own(io, human, state: dict, known: int | None = None) -> tuple[int, dict] | None:
    """Our pet in reach: `known` (a serial remembered from before) when it's there, else the first
    nearby pet whose menu says it's ours (is_own)."""
    near = pets_near(state, also=known)
    if known is not None:
        hit = next(((s, m) for _, s, m in near if s == known), None)
        if hit is not None:
            return hit
    for _, s, m in near:
        if s != known and is_own(io, human, s):
            return s, m
    return None


def mount(io, human, pet: int, timeout: float = MOUNT_WAIT_S) -> bool:
    """The stock double-click on our pet; True once we ride (the mount layer)."""
    human.wait("use")
    io.poll()
    io.send(actions.dclick(pet))
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st, _ = io.poll()
        if mounted(st):
            return True
        time.sleep(0.1)
    return False


def mount_up(io, human, known: int | None = None) -> dict:
    """Ride our pet if we don't: {ok, mounted, pet, name, dead, why}. `dead`: it's a ghost (revive
    it first: the rental room did, live). No pet in reach (or none of them ours): ok False."""
    st, _ = io.poll()
    if mounted(st):
        return {"ok": True, "mounted": True, "already": True, "pet": None, "dead": False}
    found = find_own(io, human, st, known)
    if found is None:
        return {"ok": False, "mounted": False, "pet": None, "dead": False,
                "why": f"no pet of ours within {PET_RANGE} tiles"}
    pet, m = found
    out = {"pet": f"0x{pet:08X}", "name": m.get("name")}
    if m.get("dead"):
        return {**out, "ok": False, "mounted": False, "dead": True, "why": "our pet is a ghost: revive it first"}
    ok = mount(io, human, pet)
    return {**out, "ok": ok, "mounted": ok, "dead": False,
            **({} if ok else {"why": f"no mount after the double-click within {MOUNT_WAIT_S:g} s"})}

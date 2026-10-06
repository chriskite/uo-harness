"""Trapped pouches: where the lumber runner keeps its logs, and the thief alarm
(docs/PLAN.md "Keep thieves off the logs"; docs/NOTES.md "A trapped pouch popped by its owner").

    pack_pouches(world, pack) -> {serial: {hue, container, live}}   pouches in the pack, any depth
    live(world, pack) -> [serial]          trapped (hue 38) ones, shallowest first
    contents(world, container, graphics=None) -> [(serial, item)]   items directly inside
    holding(world, pack, graphics) -> [serial]   live pouches holding any of `graphics`
    PopWatch().observe(world, pack, pos, events, now) -> [pop]

A trapped pouch is a pouch (graphic 0x0E79) with hue 38, as Errol the provisioner sells it
("Trapped Pouch", 25 gp; live 2026-10-04, session 20261004_113229 at 1:25). A double-click sets
the trap off instead of opening it [INFERENCE: RunUO TrapableContainer.OnDoubleClick runs
ExecuteTrap and opens only when no trap went off; live, no 0x24 followed either pop], whoever
double-clicks it: the owner, or a thief snooping it. After that it is an ordinary pouch (hue 0)
that opens on the next double-click.

A pop shows as, in one flush (live 2026-10-04 2:31 and 2:52, the owner's pops):
  - `sound` 0x0307 on the pouch's tile, which is ours while it is in our pack (S2C 0x54),
  - five location explosions 0x36BD (S2C 0xC0 type 2, no source or target) on the tiles around
    it: x-1, x+1, y-1, y+1 and x+1,y+1 at z+11, exactly RunUO's MagicTrap,
  - the pouch re-sent (0x25) with hue 0,
  - for the owner's pop also 1 hit lost ("-1" over our head, 0xA1) and the system line "You now
    have N trapped pouches remaining." [INFERENCE: RunUO damages the one who opened it, so a
    thief's pop costs the thief the hit, not us; the line's recipient is unmeasured.]
The world model turns the sound and the explosions within 2 tiles of us into `sound` and
`effect` events (harness/world/runtime.py NEAR_SELF); the hue is in world.items.

PopWatch sorts each signal into ours or not. Ours: a double-click (C2S 0x06, the `dclick` event,
the agent's or the client's) on that pouch while it was live, for the hue change; any such
double-click within OWN_POP_S before it, for an explosion or the sound (they don't name the
pouch). Anything else is a pop we didn't cause: the thief alarm. An explosion potion or spell
going off next to us also reads as one [INFERENCE: RunUO's explosion potion uses 0x36BD and
0x0307 too]; the response (leave) is right for those as well.
"""
from __future__ import annotations

import ledger

POUCH_GRAPHIC = 0x0E79
TRAPPED_HUE = 38
EXPLOSION = 0x36BD              # the location effect, five per pop
EXPLOSION_SOUND = 0x0307
EFFECT_AT_LOCATION = 2          # S2C 0xC0 type
POP_NEAR = 2                    # tiles from us an explosion or the sound may be (they ring our tile)
OWN_POP_S = 3.0                 # an explosion this soon after our double-click on a live pouch is ours
OWN_SKEW_S = 0.5                # the signal may carry a time slightly before the runner's own note
CARRY = 3                       # pouches a run carries: one used up a trip (lumber_opt plan, low_supplies)


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def pack_pouches(world: dict, pack: int) -> dict[int, dict]:
    """{serial: {hue, container, depth, live}} for every pouch in the pack, nested ones too."""
    items = ledger.pack_items({"world": world}, pack)
    out = {}
    for s, it in items.items():
        if it["graphic"] != POUCH_GRAPHIC:
            continue
        depth, c = 1, it["container"]
        while c != pack and c in items:
            depth, c = depth + 1, items[c]["container"]
        out[s] = {"hue": it["hue"], "container": it["container"], "depth": depth,
                  "live": it["hue"] == TRAPPED_HUE}
    return out


def live(world: dict, pack: int) -> list[int]:
    """The trapped (hue 38) pouches in the pack, shallowest first, then by serial."""
    pp = pack_pouches(world, pack)
    return sorted((s for s, p in pp.items() if p["live"]), key=lambda s: (pp[s]["depth"], s))


def contents(world: dict, container: int, graphics=None) -> list[tuple[int, dict]]:
    """[(serial, item)] directly inside `container` (of `graphics` when given)."""
    out = []
    for key, it in (world.get("items") or {}).items():
        c = it.get("container")
        if c is not None and _serial(c) == container and (graphics is None or it.get("graphic") in graphics):
            out.append((_serial(key), it))
    return sorted(out, key=lambda x: x[0])


def holding(world: dict, pack: int, graphics) -> list[int]:
    """The live pouches with any of `graphics` directly inside, in live() order."""
    return [s for s in live(world, pack) if contents(world, s, graphics)]


class PopWatch:
    """Pops of the trapped pouches in our pack, ours or not (module docstring)."""

    def __init__(self, own_s: float = OWN_POP_S):
        self.own_s = own_s
        self.live: set | None = None       # live pouches at the last observe (None before the first)
        self.own: dict[int, float] = {}    # pouch serial -> time we double-clicked it while live
        self.last_own_t: float | None = None

    def own_pop(self, serial: int, t: float):
        """We are about to double-click this live pouch (the runner says so before sending)."""
        self.own[serial] = t
        self.last_own_t = t if self.last_own_t is None else max(self.last_own_t, t)

    def _ours(self, t) -> bool:
        return (self.last_own_t is not None and t is not None
                and -OWN_SKEW_S <= t - self.last_own_t <= self.own_s)

    def observe(self, world: dict, pack: int | None, pos, events, now: float) -> list[dict]:
        """Fold the world events since the last call ([(t, event)]) and the pack now into
        pops: [{signal: 'hue' | 'explosion' | 'sound', t, own, serial (hue), x, y}].
        `pos`: our (x, y) for the explosion/sound distance (None: any distance)."""
        cur = set(live(world, pack)) if pack is not None else set()
        known = cur | (self.live or set())
        pops = []
        for t, ev in events:
            kind = ev.get("ev")
            if kind == "dclick" and ev.get("serial") in known:
                self.own_pop(ev["serial"], t)
                continue
            if kind == "effect" and ev.get("type") == EFFECT_AT_LOCATION and ev.get("graphic") == EXPLOSION:
                sig = "explosion"
            elif kind == "sound" and ev.get("sound") == EXPLOSION_SOUND:
                sig = "sound"
            else:
                continue
            x, y = ev.get("x"), ev.get("y")
            if pos is not None and (x is None or max(abs(x - pos[0]), abs(y - pos[1])) > POP_NEAR):
                continue
            pops.append({"signal": sig, "t": t, "own": self._ours(t), "serial": None, "x": x, "y": y})
        if self.live is not None and pack is not None:
            pouches = pack_pouches(world, pack)
            for s in sorted(self.live - cur):
                if s in pouches:                   # still ours, no longer trapped: it went off
                    pops.append({"signal": "hue", "t": now, "own": s in self.own, "serial": s,
                                 "x": None, "y": None, "hue": pouches[s]["hue"]})
        self.live = cur if pack is not None else self.live
        return pops


def thief_pops(pops: list[dict]) -> list[dict]:
    """The pops that are a thief at our logs: not ours, and one of our live pouches went off (its
    hue 38 -> 0). An explosion or its sound near us alone is someone else's pouch: live 2026-10-06
    (lumber-20261006-111829-94a3) a sound at the busy guild-house landing right after our recall
    stopped the run as a thief while our pouch was still armed. With the hue change, the sound and
    explosion come along as evidence."""
    alarm = [p for p in pops if not p["own"]]
    return alarm if any(p["signal"] == "hue" for p in alarm) else []

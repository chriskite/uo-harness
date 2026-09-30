"""What the overseer "sees": an ASCII map of the player's surroundings with
levels, reachability, doors, trees, ground items and mobiles (ctl map).

The map geometry is the client's walk rules (pathfind.Walk). Reachability is a
flood fill from where the player stands, with the same rules the Mover plans
with, limited to the view square. So a tile that is walkable but only reached
by leaving the view shows as not reachable ('^', 'v', ':'). Heights are
relative to the player: from a cave under a hill every tile reads ',' (your
level, with ground above it); from the hill the cave floor reads 'v' and a
ramp shows as a run of '+' or '-' leading away from '.'.

Legend (priority high to low):
  @  you             a..z  mobiles (listed in `mobiles`)
  X  route goal      o     planned route (with `to`)
  D  door            *     other ground item (listed in `items`)
  T  tree (static)
  .  reachable, your level (|dz| <= LEVEL_DZ)
  ,  reachable, your level, with another floor or ground above it (a cave
     ceiling, a roof, a hill over you)
  +  reachable, higher            -  reachable, lower
  ^  standable, not reachable here, higher
  v  standable, not reachable here, lower
  :  standable, not reachable here, your level
  #  nothing to stand on (wall, water, cliff face)

Read-only; nothing here sends anything.
"""
import collections
import string

import nav
import pathfind
import uomap

LEVEL_DZ = 5
DOOR_GRAPHICS = range(0x0675, 0x06F5)
MOBILE_KEYS = string.ascii_lowercase

LEGEND = {"@": "you", "a-z": "mobiles (see mobiles[].key)", "X": "route goal", "o": "planned route",
          "D": "door", "*": "ground item (see items)", "T": "tree",
          ".": "reachable, your level",
          ",": "reachable, your level, another floor/ground above it (cave, roof, hill over you)",
          "+": "reachable, higher", "-": "reachable, lower",
          "^": "standable but not reachable from here in view, higher",
          "v": "standable but not reachable from here in view, lower",
          ":": "standable but not reachable from here in view, your level",
          "#": "nothing to stand on (wall, water, cliff)",
          "rows": "each row starts with its y; the two header rows are x (tens digit, units digit)"}


def standable(walk, x, y) -> list[int]:
    """Heights one can stand at on the tile (surfaces and bridges)."""
    return sorted({o[3] for o in walk.objects(x, y) if o[2] & (pathfind.POF_SURFACE | pathfind.POF_BRIDGE)})


def reachable(walk, start, radius: int) -> dict:
    """{(x, y): sorted zs} reachable from start (x, y, z) by the walk rules
    without leaving the square of `radius` around start."""
    sx, sy, sz = start
    seen = {(sx, sy, sz)}
    out = collections.defaultdict(set)
    out[(sx, sy)].add(sz)
    q = collections.deque([(sx, sy, sz)])
    while q:
        x, y, z = q.popleft()
        for d in range(8):
            nxt = walk.can_walk(x, y, z, d)
            if nxt is None:
                continue
            nx, ny, nz = nxt
            if abs(nx - sx) > radius or abs(ny - sy) > radius or (nx, ny, nz) in seen:
                continue
            seen.add((nx, ny, nz))
            out[(nx, ny)].add(nz)
            q.append((nx, ny, nz))
    return {k: sorted(v) for k, v in out.items()}


def _is_door(graphic, td) -> bool:
    if graphic is None:
        return False
    if graphic in DOOR_GRAPHICS:
        return True
    it = td.item(graphic)
    return bool(it and it.flags & uomap.DOOR)


def _terrain(walk, reach, x, y, pz) -> str:
    zs = reach.get((x, y))
    if zs:
        z = min(zs, key=lambda v: abs(v - pz))
        if abs(z - pz) <= LEVEL_DZ:
            return "," if any(s > z + 2 * LEVEL_DZ for s in standable(walk, x, y)) else "."
        return "+" if z > pz else "-"
    st = standable(walk, x, y)
    if not st:
        return "#"
    z = min(st, key=lambda v: abs(v - pz))
    return ":" if abs(z - pz) <= LEVEL_DZ else ("^" if z > pz else "v")


def _route(walk, start, to, to_z, to_range):
    z_ok = (lambda z: abs(z - to_z) <= 2 * LEVEL_DZ) if to_z is not None else None
    path = pathfind.plan(walk, start, nav.within(tuple(to), to_range, z_ok))
    if path is None:
        return None, {"to": list(to), "z": to_z, "found": False}
    turns = [list(path[0])]
    for i in range(1, len(path) - 1):
        if nav.direction(path[i - 1][:2], path[i][:2]) != nav.direction(path[i][:2], path[i + 1][:2]) \
                or abs(path[i][2] - path[i - 1][2]) > LEVEL_DZ:
            turns.append(list(path[i]))
    if len(path) > 1:
        turns.append(list(path[-1]))
    return path, {"to": list(to), "z": to_z, "found": True, "steps": len(path) - 1,
                  "end": list(path[-1]), "waypoints": turns}


def render(state: dict, walk, radius: int = 12, to=None, to_z=None, to_range: int = 0,
           umap=None) -> dict:
    """The local map around the player from a state-port response.
    to: (x, y) also plans a route there; to_z: arrive within 2*LEVEL_DZ of it."""
    mv, world = state["movement"], state["world"]
    px, py, pz = mv["pos"][0], mv["pos"][1], mv["pos"][2]
    me = mv.get("self_serial")
    labels = world.get("labels") or {}
    td = walk.td
    x0, y0, x1, y1 = px - radius, py - radius, px + radius, py + radius
    inside = lambda x, y: x0 <= x <= x1 and y0 <= y <= y1  # noqa: E731
    reach = reachable(walk, (px, py, pz), radius)
    grid = {(x, y): _terrain(walk, reach, x, y, pz) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)}

    if umap is not None:
        for x, y, _z, _g in umap.find_trees(x0, y0, x1, y1):
            grid[(x, y)] = "T"

    items = []
    for key, it in (world.get("items") or {}).items():
        x, y, g = it.get("x"), it.get("y"), it.get("graphic")
        if it.get("container") is not None or x is None or not inside(x, y):
            continue
        tdi = td.item(g) if g is not None else None
        door = _is_door(g, td)
        grid[(x, y)] = "D" if door else "*"
        items.append({"serial": key, "graphic": None if g is None else f"0x{g:04X}",
                      "name": it.get("name") or (tdi.name if tdi else None), "door": door,
                      "at": [x, y], "z": it.get("z"), "amount": it.get("amount"),
                      "dist": nav.chebyshev((x, y), (px, py))})
    items.sort(key=lambda i: i["dist"])

    route = None
    if to is not None:
        path, route = _route(walk, (px, py, pz), to, to_z, to_range)
        for x, y, _z in (path or [])[1:]:
            if inside(x, y) and grid[(x, y)] != "D":
                grid[(x, y)] = "o"
        if path and inside(*path[-1][:2]):
            grid[tuple(path[-1][:2])] = "X"

    cands = sorted((nav.chebyshev((m["x"], m["y"]), (px, py)), key, m)
                   for key, m in (world.get("mobiles") or {}).items()
                   if m.get("x") is not None and int(key, 16) != me and inside(m["x"], m["y"]))
    mobiles = []
    for i, (dist, key, m) in enumerate(cands):
        mk = MOBILE_KEYS[i] if i < len(MOBILE_KEYS) else None
        x, y, z = m["x"], m["y"], m.get("z")
        if mk:
            grid[(x, y)] = mk
        mobiles.append({"key": mk, "serial": key, "name": m.get("name"), "label": labels.get(key),
                        "notoriety": m.get("notoriety"), "at": [x, y], "z": z,
                        "dz": None if z is None else z - pz, "dist": dist,
                        "on_reachable_level": z is not None and any(abs(v - z) <= LEVEL_DZ
                                                                    for v in reach.get((x, y), ()))})
    grid[(px, py)] = "@"

    xs = range(x0, x1 + 1)
    here = standable(walk, px, py)
    return {"ok": True, "center": [px, py, pz], "facet": (world.get("self") or {}).get("map"),
            "radius": radius, "north": "up",
            "under_cover": any(s > pz + 2 * LEVEL_DZ for s in here),
            "levels_above_you": [s for s in here if s > pz + 2 * LEVEL_DZ],
            "levels_below_you": [s for s in here if s < pz - 2 * LEVEL_DZ],
            "map": ["      " + "".join(str(x // 10 % 10) for x in xs),
                    "      " + "".join(str(x % 10) for x in xs)]
            + [f"{y:5d} " + "".join(grid[(x, y)] for x in xs) for y in range(y0, y1 + 1)],
            "legend": LEGEND,
            "reachable_levels": dict(sorted(collections.Counter(z for zs in reach.values() for z in zs).items())),
            "mobiles": mobiles, "items": items[:40], "route": route}

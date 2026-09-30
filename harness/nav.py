"""Walk memory + route planner for the 8-neighbour UO tile grid.

There is no map/collision data in the harness. The only evidence of
walkability is movement the server confirmed in past sessions:

  * raw captures (logs/session_*.{c2s,s2c}.raw): replay the S2C packets in
    order, tracking our position from self anchors (0x1B login confirm sets
    the self serial; 0x20 / 0x77 carrying the self serial; 0x21 walk deny).
    Each walk confirm `22 <seq> ..` is matched to the next C2S walk
    `02 <dir|0x80> <seq> <key>` with that seq, in C2S order (walks skipped
    over were rejected). A walk whose direction differs from the facing only
    turns; otherwise it moves one tile: record both tiles and the directed
    edge. Server denies in raw captures are NOT turned into blocked moves: a
    deny can be pacing/sequence related, and a false wall would cut real
    routes. Blocked moves come only from the proxy's explicit rows.
  * proxy jsonl rows `{"ev":"step","from":[x,y],"to":[x,y],"z":z}` (a
    confirmed move) and `{"ev":"blocked","from":[x,y],"dir":d}` (a
    server-denied move).

`plan()` is an optimistic A*: a known edge (either direction) costs 1, a known
tile 1.5, an unknown tile 4, so it prefers proven ground but still routes
through the unknown when it must.

CLI:
  python harness/nav.py build [--logdir logs] [--out harness/data/walkmem.json]
  python harness/nav.py plan x1,y1 x2,y2 [--radius R] [--mem harness/data/walkmem.json]
"""
from __future__ import annotations

import argparse
import glob
import heapq
import itertools
import json
import os
import sys
from typing import Callable, Iterable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from uo.packets import packet_length, C2S_OVERRIDES
from uo.s2c import PRELUDE_LEN, S2CStream, prelude_keys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LOGDIR = os.path.join(ROOT, "logs")
DEFAULT_MEM = os.path.join(ROOT, "harness", "data", "walkmem.json")
CLIENT_PREAMBLE_LEN = 5

# 0=N 1=NE 2=E 3=SE 4=S 5=SW 6=W 7=NW
DIR_DELTA = ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1))
_DELTA_DIR = {d: i for i, d in enumerate(DIR_DELTA)}

COST_EDGE = 1.0
COST_KNOWN = 1.5
COST_UNKNOWN = 4.0

Tile = tuple[int, int]


def direction(a: Tile, b: Tile) -> int:
    """Direction 0..7 of the single step a -> b; ValueError if not 8-adjacent."""
    try:
        return _DELTA_DIR[(b[0] - a[0], b[1] - a[1])]
    except KeyError:
        raise ValueError(f"{a} -> {b} is not a single step") from None


def step(a: Tile, d: int) -> Tile:
    """The tile one step from a in direction d."""
    dx, dy = DIR_DELTA[d]
    return (a[0] + dx, a[1] + dy)


def chebyshev(a: Tile, b: Tile) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


class WalkMemory:
    """Server-proven walkability: tiles stood on, directed confirmed moves,
    and server-denied (tile, dir) moves."""

    def __init__(self):
        self.tiles: set[Tile] = set()
        self.edges: set[tuple[Tile, Tile]] = set()
        self.blocked: set[tuple[Tile, int]] = set()

    def add_step(self, a: Tile, b: Tile) -> None:
        a, b = (int(a[0]), int(a[1])), (int(b[0]), int(b[1]))
        direction(a, b)  # validates adjacency
        self.tiles.add(a)
        self.tiles.add(b)
        self.edges.add((a, b))

    def add_blocked(self, a: Tile, d: int) -> None:
        d = int(d)
        if not 0 <= d <= 7:
            raise ValueError(f"direction {d} out of range")
        self.blocked.add(((int(a[0]), int(a[1])), d))

    def merge(self, other: "WalkMemory") -> "WalkMemory":
        self.tiles |= other.tiles
        self.edges |= other.edges
        self.blocked |= other.blocked
        return self

    def bbox(self) -> tuple[int, int, int, int] | None:
        """(min_x, min_y, max_x, max_y) over tiles, None if empty."""
        if not self.tiles:
            return None
        xs = [t[0] for t in self.tiles]
        ys = [t[1] for t in self.tiles]
        return min(xs), min(ys), max(xs), max(ys)

    def stats(self) -> dict:
        return {"tiles": len(self.tiles), "edges": len(self.edges),
                "blocked": len(self.blocked), "bbox": self.bbox()}

    def to_json(self) -> str:
        """Deterministic JSON, one entry per line (diff-friendly)."""
        def block(name, rows, last=False):
            body = ",\n".join("    " + json.dumps(r) for r in rows)
            inner = f"[\n{body}\n  ]" if rows else "[]"
            return f'  "{name}": {inner}' + ("" if last else ",")
        tiles = sorted([x, y] for x, y in self.tiles)
        edges = sorted([a[0], a[1], b[0], b[1]] for a, b in self.edges)
        blocked = sorted([a[0], a[1], d] for a, d in self.blocked)
        return "\n".join(["{", '  "version": 1,', block("tiles", tiles),
                          block("edges", edges), block("blocked", blocked, last=True),
                          "}"]) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "WalkMemory":
        doc = json.loads(text)
        m = cls()
        m.tiles = {(int(x), int(y)) for x, y in doc.get("tiles", ())}
        for x1, y1, x2, y2 in doc.get("edges", ()):
            m.add_step((x1, y1), (x2, y2))
        for x, y, d in doc.get("blocked", ()):
            m.add_blocked((x, y), d)
        return m

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.to_json())

    @classmethod
    def load(cls, path: str) -> "WalkMemory":
        with open(path, encoding="utf-8") as f:
            return cls.from_json(f.read())


# ---------------------------------------------------------------------------
# Reconstruction from logs
# ---------------------------------------------------------------------------

def c2s_walks(c2s_raw: bytes, c2s_key: int) -> list[tuple[int, int]]:
    """[(dir 0..7, seq)] of every C2S walk (0x02) in a raw C2S capture."""
    buf = bytes(b ^ c2s_key for b in c2s_raw[CLIENT_PREAMBLE_LEN:])
    mv = memoryview(buf)
    walks = []
    i, n = 0, len(buf)
    while i < n:
        plen = packet_length(mv[i:], overrides=C2S_OVERRIDES)
        if plen == 0:
            break  # truncated tail
        if plen < 0:
            i += 1  # implausible length: drop a byte and resync
            continue
        if buf[i] == 0x02 and plen >= 3:
            walks.append((buf[i + 1] & 7, buf[i + 2]))
        i += plen
    return walks


def _u32(p: bytes, o: int) -> int:
    return int.from_bytes(p[o:o + 4], "big")


def reconstruct_session(c2s_raw: bytes, s2c_raw: bytes,
                        memory: WalkMemory | None = None,
                        stats: dict | None = None) -> WalkMemory:
    """Add the confirmed moves of one raw capture pair to memory (see module doc).

    Moves are held until the next self anchor: if the anchor agrees with the
    dead-reckoned position they are committed, otherwise (drift: mismatched
    walk/confirm pairing, or a teleport) the whole segment is dropped, so a
    pairing error never invents a walkable edge. Moves after the last anchor
    are committed. `stats`, if given, accumulates moves/committed/dropped/drifts.
    Garbage/short input yields no moves rather than an exception."""
    memory = memory if memory is not None else WalkMemory()
    st = stats if stats is not None else {}
    for k in ("moves", "committed", "dropped", "drifts"):
        st.setdefault(k, 0)
    try:
        s2c_key, c2s_key = prelude_keys(s2c_raw[:PRELUDE_LEN])
    except ValueError:
        return memory
    walks = c2s_walks(c2s_raw, c2s_key)
    if not walks:
        return memory
    wi = 0
    me = None
    pos: Tile | None = None
    facing: int | None = None
    pending: list[tuple[Tile, Tile]] = []

    def commit():
        for a, b in pending:
            memory.add_step(a, b)
        st["committed"] += len(pending)
        pending.clear()

    # Outlands widens coordinates to u32 in all four self-position packets
    for _, p in S2CStream(s2c_key).feed(s2c_raw[PRELUDE_LEN:]):
        if not p:
            continue
        pid = p[0]
        anchor = None
        if pid == 0x1B and len(p) >= 26:
            me = _u32(p, 1)
            anchor = (_u32(p, 13), _u32(p, 17)), p[25] & 7
        elif pid == 0x20 and len(p) >= 28 and me is not None and _u32(p, 1) == me:
            anchor = (_u32(p, 13), _u32(p, 17)), p[23] & 7
        elif pid == 0x77 and len(p) >= 18 and me is not None and _u32(p, 1) == me:
            anchor = (_u32(p, 5), _u32(p, 9)), p[17] & 7
        elif pid == 0x21 and len(p) >= 15:
            anchor = (_u32(p, 2), _u32(p, 6)), p[10] & 7
        elif pid == 0x22 and len(p) >= 3:
            seq = p[1]
            while wi < len(walks) and walks[wi][1] != seq:
                wi += 1  # rejected / unconfirmed walk
            if wi >= len(walks):
                break
            d = walks[wi][0]
            wi += 1
            if pos is None:
                continue
            if d != facing:
                facing = d
                continue
            nxt = step(pos, d)
            pending.append((pos, nxt))
            st["moves"] += 1
            pos = nxt
        if anchor is not None:
            if pos is not None and anchor[0] != pos:
                st["drifts"] += 1
                st["dropped"] += len(pending)
                pending.clear()
            else:
                commit()
            pos, facing = anchor
    commit()
    return memory


def apply_log_row(memory: WalkMemory, row: dict) -> bool:
    """Apply one proxy jsonl row (`step` / `blocked`); False if not applicable."""
    ev = row.get("ev")
    try:
        if ev == "step":
            memory.add_step(tuple(row["from"]), tuple(row["to"]))
        elif ev == "blocked":
            memory.add_blocked(tuple(row["from"]), row["dir"])
        else:
            return False
    except (KeyError, TypeError, ValueError, IndexError):
        return False
    return True


def build_from_logs(logdir: str = DEFAULT_LOGDIR, stats: dict | None = None) -> WalkMemory:
    """WalkMemory from every capture pair with a non-empty c2s.raw plus every
    jsonl step/blocked row under logdir. Unreadable or garbage files add nothing.
    `stats`, if given, receives the reconstruct_session counters plus `rows`."""
    memory = WalkMemory()
    st = stats if stats is not None else {}
    st.setdefault("rows", 0)
    for s2c_path in sorted(glob.glob(os.path.join(logdir, "session_*.s2c.raw"))):
        c2s_path = s2c_path[:-len(".s2c.raw")] + ".c2s.raw"
        try:
            if os.path.getsize(c2s_path) <= CLIENT_PREAMBLE_LEN:
                continue  # lost C2S buffer: confirms can't be matched to walks
            with open(c2s_path, "rb") as f:
                c2s_raw = f.read()
            with open(s2c_path, "rb") as f:
                s2c_raw = f.read()
        except OSError:
            continue
        reconstruct_session(c2s_raw, s2c_raw, memory, st)
    for jl in sorted(glob.glob(os.path.join(logdir, "session_*.jsonl"))):
        try:
            with open(jl, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"step"' not in line and '"blocked"' not in line:
                        continue  # cheap pre-filter; logs are large
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and apply_log_row(memory, row):
                        st["rows"] += 1
        except OSError:
            continue
    return memory


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

class within:
    """Goal predicate: Chebyshev distance to `center` <= `radius`. Carries an
    admissible `heuristic` that plan() uses; a bare callable goal gets h = 0."""

    __slots__ = ("center", "radius")

    def __init__(self, center: Tile, radius: int = 0):
        self.center = (int(center[0]), int(center[1]))
        self.radius = int(radius)

    def __call__(self, t: Tile) -> bool:
        return chebyshev(t, self.center) <= self.radius

    def heuristic(self, t: Tile) -> int:
        return max(0, chebyshev(t, self.center) - self.radius)


def move_cost(memory: WalkMemory, a: Tile, b: Tile) -> float:
    """Cost of the single step a -> b: known edge (either way) 1, known tile
    1.5, unknown tile 4."""
    if (a, b) in memory.edges or (b, a) in memory.edges:
        return COST_EDGE
    if b in memory.tiles:
        return COST_KNOWN
    return COST_UNKNOWN


def plan(memory: WalkMemory, start: Tile, goal_fn: Callable[[Tile], bool],
         extra_blocked: Iterable = (), max_expand: int = 20000,
         cost_scale: Callable[[Tile, Tile], float] | None = None) -> list[Tile] | None:
    """Cheapest 8-neighbour tile path from start to the first tile satisfying
    goal_fn, including start; None if unreachable within max_expand expansions.

    Forbidden: moves in memory.blocked or extra_blocked (items `((x, y), dir)`),
    entering tiles listed in extra_blocked as bare `(x, y)`, and diagonals whose
    either orthogonal component move from the same tile is blocked (no corner
    cutting). Heuristic: goal_fn.heuristic if present (see `within`), else 0.
    cost_scale(a, b) >= 1 multiplies each step's cost (humanize.Human route
    noise); it keeps the heuristic admissible.
    """
    start = (int(start[0]), int(start[1]))
    blocked_moves = set(memory.blocked)
    blocked_tiles: set[Tile] = set()
    for item in extra_blocked:
        if isinstance(item[0], (tuple, list)):
            blocked_moves.add(((int(item[0][0]), int(item[0][1])), int(item[1])))
        else:
            blocked_tiles.add((int(item[0]), int(item[1])))
    h = getattr(goal_fn, "heuristic", None) or (lambda t: 0)

    tie = itertools.count()
    g_best = {start: 0.0}
    parent: dict[Tile, Tile] = {}
    heap = [(h(start), next(tie), start)]
    closed: set[Tile] = set()
    expanded = 0
    while heap:
        _, _, cur = heapq.heappop(heap)
        if cur in closed:
            continue
        if goal_fn(cur):
            path = [cur]
            while cur in parent:
                cur = parent[cur]
                path.append(cur)
            return path[::-1]
        closed.add(cur)
        expanded += 1
        if expanded > max_expand:
            return None
        g = g_best[cur]
        for d in range(8):
            if (cur, d) in blocked_moves:
                continue
            if d & 1 and ((cur, (d - 1) % 8) in blocked_moves
                          or (cur, (d + 1) % 8) in blocked_moves):
                continue
            nxt = step(cur, d)
            if nxt in closed or nxt in blocked_tiles:
                continue
            ng = g + move_cost(memory, cur, nxt) * (cost_scale(cur, nxt) if cost_scale else 1.0)
            if ng < g_best.get(nxt, float("inf")):
                g_best[nxt] = ng
                parent[nxt] = cur
                heapq.heappush(heap, (ng + h(nxt), next(tie), nxt))
    return None


def path_breakdown(memory: WalkMemory, path: list[Tile]) -> dict:
    """Counts of steps along known edges / into known tiles / into unknown tiles."""
    out = {"edge": 0, "known": 0, "unknown": 0}
    names = {COST_EDGE: "edge", COST_KNOWN: "known", COST_UNKNOWN: "unknown"}
    for a, b in zip(path, path[1:]):
        out[names[move_cost(memory, a, b)]] += 1
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _tile_arg(s: str) -> Tile:
    x, y = s.split(",")
    return int(x, 0), int(y, 0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="rebuild walk memory from logs")
    b.add_argument("--logdir", default=DEFAULT_LOGDIR)
    b.add_argument("--out", default=DEFAULT_MEM)
    p = sub.add_parser("plan", help="plan a route x1,y1 -> x2,y2")
    p.add_argument("start", type=_tile_arg)
    p.add_argument("goal", type=_tile_arg)
    p.add_argument("--radius", type=int, default=0, help="Chebyshev goal radius")
    p.add_argument("--mem", default=DEFAULT_MEM)
    args = ap.parse_args(argv)

    if args.cmd == "build":
        st: dict = {}
        mem = build_from_logs(args.logdir, st)
        mem.save(args.out)
        s = mem.stats()
        print(f"raw moves {st.get('moves', 0)} (committed {st.get('committed', 0)}, "
              f"dropped {st.get('dropped', 0)} over {st.get('drifts', 0)} anchor drifts); "
              f"jsonl rows {st['rows']}")
        print(f"tiles {s['tiles']}  edges {s['edges']}  blocked {s['blocked']}  "
              f"bbox {s['bbox']}  -> {args.out}")
        return 0

    mem = WalkMemory.load(args.mem)
    path = plan(mem, args.start, within(args.goal, args.radius))
    if path is None:
        print(f"no route {args.start} -> {args.goal} (radius {args.radius})")
        return 1
    bd = path_breakdown(mem, path)
    print(f"{len(path) - 1} steps  (known edges {bd['edge']}, known tiles {bd['known']}, "
          f"unknown tiles {bd['unknown']})")
    print("dirs  " + "".join(str(direction(a, b)) for a, b in zip(path, path[1:])))
    print("path  " + " ".join(f"{x},{y}" for x, y in path))
    return 0


if __name__ == "__main__":
    sys.exit(main())

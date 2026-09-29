"""Tests for harness/nav.py: walk-memory reconstruction and the A* planner.

Ground truth: logs/session_20260929_144541.* (committed capture; its first six
walks go west from the login spot 0x7AB,0xA25 to 0x7A6,0xA25) and the committed
harness/data/walkmem.json. Planner tests use small hand-built memories.

Run: python harness/test_nav.py   (no network, well under 5 s)
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nav
from nav import WalkMemory, direction, plan, step, within

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "logs")
FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def eq(name, got, want):
    check(name, got == want, f"(got {got!r}, want {want!r})")


def path_cost(mem, path):
    return sum(nav.move_cost(mem, a, b) for a, b in zip(path, path[1:]))


def valid_path(path):
    """Every consecutive pair is one 8-neighbour step."""
    try:
        for a, b in zip(path, path[1:]):
            direction(a, b)
    except ValueError:
        return False
    return True


def chain(mem, tiles):
    for a, b in zip(tiles, tiles[1:]):
        mem.add_step(a, b)
    return mem


# ---------------------------------------------------------------------------

def test_direction():
    print("direction")
    for d in range(8):
        eq(f"step/direction roundtrip {d}", direction((10, 10), step((10, 10), d)), d)
    eq("north is -y", step((5, 5), 0), (5, 4))
    for bad in ((10, 10), (12, 10), (10, 13)):
        try:
            direction((10, 10), bad)
            check(f"non-adjacent {bad} rejected", False)
        except ValueError:
            check(f"non-adjacent {bad} rejected", True)


def test_reconstruct_144541():
    print("reconstruct session_20260929_144541")
    base = os.path.join(LOGS, "session_20260929_144541")
    with open(base + ".c2s.raw", "rb") as f:
        c2s = f.read()
    with open(base + ".s2c.raw", "rb") as f:
        s2c = f.read()
    st = {}
    mem = nav.reconstruct_session(c2s, s2c, None, st)
    check("tile 0x7A6,0xA25 walked", (0x7A6, 0xA25) in mem.tiles)
    y = 0xA25
    missing = [x for x in range(0x7AB, 0x7A6, -1) if ((x, y), (x - 1, y)) not in mem.edges]
    eq("west edge chain 0x7AB -> 0x7A6 at y=0xA25", missing, [])
    check("chain is directed (no reverse edge invented)",
          ((0x7A6, y), (0x7A7, y)) not in mem.edges)
    eq("clean session: no anchor drift", st["drifts"], 0)
    eq("all moves committed", st["committed"], st["moves"])
    eq("no blocked moves derived from raw captures", mem.blocked, set())


def test_reconstruct_garbage():
    print("reconstruct robustness")
    eq("empty inputs", nav.reconstruct_session(b"", b"").stats()["tiles"], 0)
    eq("garbage prelude", nav.reconstruct_session(b"\x01" * 50, b"\x00" * 50).stats()["tiles"], 0)
    base = os.path.join(LOGS, "session_20260929_144541")
    with open(base + ".s2c.raw", "rb") as f:
        s2c = f.read()
    eq("valid S2C, empty C2S", nav.reconstruct_session(b"", s2c).stats()["tiles"], 0)
    # truncated S2C: must not raise
    mem = nav.reconstruct_session(b"\x00" * 5, s2c[:len(s2c) // 3])
    check("truncated S2C does not raise", isinstance(mem, WalkMemory))


def test_jsonl_rows_and_build():
    print("build_from_logs: jsonl rows + garbage files")
    rows = [
        {"ev": "step", "from": [100, 100], "to": [101, 100], "z": 0, "t": 1.0},
        {"ev": "step", "from": [101, 100], "to": [102, 101], "z": 0},
        {"ev": "blocked", "from": [102, 101], "dir": 2},
        {"ev": "step", "from": [0, 0], "to": [5, 5]},        # not adjacent: ignored
        {"ev": "blocked", "from": [1, 1], "dir": 9},          # bad dir: ignored
        {"ev": "step", "from": [1]},                          # malformed: ignored
        {"ev": "c2s", "hex": "02"},                           # unrelated
    ]
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "session_x.jsonl"), "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
            f.write('{"ev": "step", "from": [7, 7], "to"\n')  # torn line
            f.write("not json at all \"step\"\n")
        with open(os.path.join(td, "session_y.s2c.raw"), "wb") as f:
            f.write(b"\xde\xad\xbe\xef" * 20)
        with open(os.path.join(td, "session_y.c2s.raw"), "wb") as f:
            f.write(b"\x01" * 40)
        open(os.path.join(td, "session_z.s2c.raw"), "wb").close()
        open(os.path.join(td, "session_z.c2s.raw"), "wb").close()
        st = {}
        mem = nav.build_from_logs(td, st)
    eq("applied rows", st["rows"], 3)
    eq("tiles from step rows", mem.tiles, {(100, 100), (101, 100), (102, 101)})
    eq("edges from step rows", mem.edges, {((100, 100), (101, 100)), ((101, 100), (102, 101))})
    eq("blocked from blocked row", mem.blocked, {((102, 101), 2)})

    base = chain(WalkMemory(), [(99, 100), (100, 100)])
    base.merge(mem)
    check("merge keeps both sources",
          ((99, 100), (100, 100)) in base.edges and ((102, 101), 2) in base.blocked)
    p = plan(base, (102, 101), within((103, 101)))
    check("merged blocked row steers the planner",
          p is not None and p[1] != (103, 101), f"{p}")


def test_save_load():
    print("save/load")
    mem = chain(WalkMemory(), [(1, 1), (2, 1), (2, 2)])
    mem.add_blocked((2, 2), 4)
    mem.tiles.add((9, 9))
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "sub", "m.json")
        mem.save(path)
        back = WalkMemory.load(path)
        with open(path) as f:
            text = f.read()
    eq("tiles roundtrip", back.tiles, mem.tiles)
    eq("edges roundtrip", back.edges, mem.edges)
    eq("blocked roundtrip", back.blocked, mem.blocked)
    eq("serialisation deterministic", back.to_json(), text)


def test_plan_prefers_known():
    print("plan: cost model")
    known = [(0, 0), (0, 1), (0, 2), (1, 3), (2, 3), (3, 3), (4, 2), (4, 1), (4, 0)]
    mem = chain(WalkMemory(), known)
    p = plan(mem, (0, 0), within((4, 0)))
    eq("8-edge known detour beats 4-step unknown shortcut", p, known)
    eq("its cost", path_cost(mem, p), 8.0)

    long_known = [(0, 0)] + [(0, y) for y in range(1, 10)] + [(x, 9) for x in range(1, 5)] \
        + [(4, y) for y in range(8, -1, -1)]
    mem2 = chain(WalkMemory(), long_known)
    p2 = plan(mem2, (0, 0), within((4, 0)))
    check("unknown shortcut wins when the known detour costs more",
          p2 is not None and len(p2) - 1 == 4, f"{p2}")
    check("edges usable in reverse at cost 1",
          plan(mem, (4, 0), within((0, 0))) == known[::-1])


def test_plan_blocked():
    print("plan: blocked moves")
    mem = chain(WalkMemory(), [(0, 0), (1, 0), (2, 0)])
    mem.add_blocked((1, 0), 2)  # E from (1,0) denied
    p = plan(mem, (0, 0), within((2, 0)))
    check("route found around the blocked move", p is not None and valid_path(p), f"{p}")
    check("blocked move never taken",
          all(not (a == (1, 0) and direction(a, b) == 2) for a, b in zip(p, p[1:])), f"{p}")
    p = plan(WalkMemory(), (0, 0), within((2, 0)), extra_blocked=[((0, 0), 2), (1, 1), (1, -1)])
    check("extra_blocked moves and tiles honoured",
          p is not None and p[1] not in ((1, 0), (1, 1), (1, -1)), f"{p}")


def test_plan_no_corner_cut():
    print("plan: no corner cutting")
    for blocked_dir in (0, 2):  # N or E blocked -> NE diagonal forbidden
        mem = WalkMemory()
        mem.add_blocked((0, 0), blocked_dir)
        p = plan(mem, (0, 0), within((1, -1)))
        check(f"NE diagonal refused when dir {blocked_dir} blocked",
              p is not None and len(p) == 3 and p[1] != (1, -1), f"{p}")
    p = plan(WalkMemory(), (0, 0), within((1, -1)))
    eq("NE diagonal allowed when nothing blocked", p, [(0, 0), (1, -1)])


def test_plan_enclosed():
    print("plan: unreachable")
    mem = WalkMemory()
    for d in range(8):
        mem.add_blocked((0, 0), d)
    eq("all eight moves blocked -> None", plan(mem, (0, 0), within((5, 5))), None)
    ring = [(x, y) for x in range(-2, 3) for y in range(-2, 3) if max(abs(x), abs(y)) == 2]
    eq("walled-in by extra_blocked tiles -> None",
       plan(WalkMemory(), (0, 0), within((10, 0)), extra_blocked=ring), None)
    eq("max_expand bounds the search",
       plan(WalkMemory(), (0, 0), lambda t: t == (1000, 1000), max_expand=50), None)
    eq("start already satisfies goal", plan(WalkMemory(), (3, 3), within((5, 5), 2)), [(3, 3)])


def test_plan_unknown():
    print("plan: through unknown ground")
    p = plan(WalkMemory(), (0, 0), within((5, 3)))
    check("route over unknown tiles", p is not None and valid_path(p) and p[-1] == (5, 3), f"{p}")
    eq("Chebyshev-optimal length", len(p) - 1, 5)
    mem = chain(WalkMemory(), [(0, 0), (1, 0)])
    p = plan(mem, (0, 0), within((6, 0)))
    check("known prefix then unknown", p is not None and p[:2] == [(0, 0), (1, 0)] and p[-1] == (6, 0),
          f"{p}")


def test_generated_memory():
    print("generated harness/data/walkmem.json")
    mem = WalkMemory.load(nav.DEFAULT_MEM)
    check("memory non-trivial", len(mem.tiles) > 300 and len(mem.edges) > 350, f"{mem.stats()}")
    login, bank = (1963, 2597), (1954, 2581)
    check("login spot is known ground", login in mem.tiles)
    goal = within(bank, 12)
    p = plan(mem, login, goal)
    check("route login -> within 12 of bank area", p is not None and valid_path(p) and goal(p[-1]), f"{p}")
    bd = nav.path_breakdown(mem, p)
    eq("outbound route uses no unknown tiles", bd["unknown"], 0)
    back = plan(mem, (1955, 2575), within(login))
    check("route back to login", back is not None and valid_path(back) and back[-1] == login, f"{back}")
    # admissibility: A* with the Chebyshev heuristic finds the same cost as Dijkstra
    dijkstra = plan(mem, (1955, 2575), lambda t: t == login)
    eq("A* cost == Dijkstra cost", path_cost(mem, back), path_cost(mem, dijkstra))


TESTS = [test_direction, test_reconstruct_144541, test_reconstruct_garbage,
         test_jsonl_rows_and_build, test_save_load, test_plan_prefers_known,
         test_plan_blocked, test_plan_no_corner_cut, test_plan_enclosed,
         test_plan_unknown, test_generated_memory]


def main():
    for t in TESTS:
        t()
    print(f"\nnav: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

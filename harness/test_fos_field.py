"""The Field of Souls field map (harness/fos_field.py): what the proxy records in the field, and the
reaper heatmap's cells built from it (docs/VISUALIZER.md §2.17).

Run: python harness/test_fos_field.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fos_field as ff  # noqa: E402
import memory  # noqa: E402
import proxy  # noqa: E402
import viz_feed  # noqa: E402
from sim import packets as P  # noqa: E402

FAILURES = []
ME, REAPER_A, REAPER_B, REAPER_C, CORPSE_A, CORPSE_B = 0x00104A60, 0x0004A001, 0x0004A002, 0x0004A003, 0x41000001, 0x41000002
HX, HY = ff.HARD_OFFSET
NAMED = "If you put the spirit of Remon Stace to rest, you will receive a 33.0% bonus to all further points earned this session."


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAILURES.append(name)


def field_tap():
    t = proxy.SessionTap(viz_feed._Null(), viz_feed._Null(), viz_feed._Null())
    t.field = ff.FieldLog()
    clock = [1000.0]
    t.wall = lambda: clock[0]
    return t, clock


def logged(t, ev):
    return [e["data"] for e in t.events if e["data"].get("ev") == ev]


def reaper(t, serial, x, y):
    t._world(proxy.S2C, P.mobile(serial, 400, x, y, 0))
    t._world(proxy.S2C, P.says(serial, "a reaper of souls", "a reaper of souls", kind=6, hue=946, body=400))


def test_field_log():
    print("== in the field: the session, monsters first seen (spawned or walked in), corpses (died in view or not) ==")
    t, clock = field_tap()
    t._world(proxy.S2C, P.login(ME, 400, 7186, 756, 0))                 # the Hard field's entry (live 17:27:07)
    t.world.state.self.map = 0                                            # (the 0xBF sub 8 that sets it, as live)
    clock[0] += 3
    t._world(proxy.S2C, P.system_text(NAMED))
    s = logged(t, "fos_session")
    check("named on entry: fos_session with the instance", [(x["instance"], x["spirit"]) for x in s] == [("hard", "Remon Stace")],
          str(s))
    clock[0] += 5
    reaper(t, REAPER_A, 7186 + 18, 756)                                    # at the view's edge: walked in
    reaper(t, REAPER_B, 7186 - 6, 756 - 3)                                # inside the view: spawned there
    reaper(t, REAPER_B, 7186 - 6, 756 - 3)                                # its label again (back in view)
    mobs = [(m["serial"], m["dist"], m["spawn"]) for m in logged(t, "fos_mob")]
    check("one fos_mob per reaper: 18 off walked in, 6 off spawned",
          mobs == [("0x0004A001", 18, False), ("0x0004A002", 6, True)], str(mobs))
    t._world(proxy.S2C, P.mobile(ME, 400, 7186 - 60, 700, 0))             # a jump of ours (no steps between)
    reaper(t, REAPER_C, 7186 - 60 + 5, 700)
    check("first seen inside the view just after we arrived: not a spawn (the view filling in)",
          logged(t, "fos_mob")[-1]["spawn"] is False, str(logged(t, "fos_mob")[-1]))
    t._world(proxy.S2C, P.display_death(REAPER_C, CORPSE_A))              # 0xAF: died in view
    t._world(proxy.S2C, P.ground_item(CORPSE_A, 0x2006, 400, 7186 - 55, 700, 0))
    t._world(proxy.S2C, P.corpse_flags(CORPSE_A, REAPER_C, "a reaper of souls corpse"))
    clock[0] += 60
    t._world(proxy.S2C, P.ground_item(CORPSE_B, 0x2006, 400, 7186 - 50, 705, 0))   # a corpse coming into view
    t._world(proxy.S2C, P.corpse_flags(CORPSE_B, 0x0004A0FF, "a reaper of souls corpse"))
    t._world(proxy.S2C, P.corpse_flags(CORPSE_B, 0x0004A0FF, "a reaper of souls corpse"))   # re-sent
    corpses = [(c["corpse"], c["in_view"], c["x"]) for c in logged(t, "fos_corpse")]
    check("corpses: one each, in view after its 0xAF, not in view when it only came into view",
          corpses == [("0x41000001", True, 7131), ("0x41000002", False, 7136)], str(corpses))
    t._world(proxy.S2C, P.mobile(ME, 400, 1500, 1600, 0))                 # back in town
    reaper(t, 0x0004A010, 1505, 1600)                                      # a name, outside the field
    check("outside the field: nothing", len(logged(t, "fos_mob")) == 3)


def test_reaper_map():
    print("== the heatmap's cells: time watched within the radii, spawns and in-view deaths, both instances in one frame ==")
    path = os.path.join(tempfile.mkdtemp(prefix="fos_"), "harness.db")
    m = memory.Memory(path)
    con = m.con
    t0 = ff.EVENT_START + 3600
    rows = []

    def ev(sid, seq, t, data):
        rows.append((sid, seq, t, "proxy", data["ev"], json.dumps(data)))
    for sid, (dx, dy) in ((1, (0, 0)), (2, ff.HARD_OFFSET)):              # one Normal run, one Hard run
        con.execute("INSERT INTO sessions(id, tag, source, started) VALUES(?,?,?,?)", (sid, f"s{sid}", "live", t0))
        base = t0 + sid * 10_000
        ev(sid, 0, base, {"ev": "fos_session", "instance": "x", "spirit": "S"})
        ev(sid, 1, base + 1, {"ev": "step", "from": [6299 + dx, 700 + dy], "to": [6300 + dx, 700 + dy], "z": 0})
        ev(sid, 2, base + 101, {"ev": "step", "from": [6300 + dx, 700 + dy], "to": [6300 + dx, 701 + dy], "z": 0})
        ev(sid, 3, base + 102, {"ev": "step", "from": [6300 + dx, 701 + dy], "to": [1500, 1600], "z": 0})   # recalled out
        ev(sid, 4, base + 50, {"ev": "fos_mob", "name": ff.REAPER, "x": 6302 + dx, "y": 702 + dy, "dist": 2, "spawn": True})
        ev(sid, 5, base + 60, {"ev": "fos_mob", "name": ff.REAPER, "x": 6318 + dx, "y": 700 + dy, "dist": 18, "spawn": False})
        ev(sid, 6, base + 70, {"ev": "fos_corpse", "name": ff.REAPER, "x": 6303 + dx, "y": 701 + dy, "in_view": True})
        ev(sid, 7, base + 71, {"ev": "fos_corpse", "name": ff.REAPER, "x": 6303 + dx, "y": 701 + dy, "in_view": False})
        ev(sid, 8, base + 72, {"ev": "fos_corpse", "name": "a haunted hayman", "x": 6303 + dx, "y": 701 + dy, "in_view": True})
    con.executemany("INSERT INTO events VALUES(?,?,?,?,?,?)", rows)
    con.commit()
    r = ff.reaper_map(con, now=t0 + 50_000)
    m.close()
    cells = {(c[0], c[1]): c[2:] for c in r["cells"]}
    here = cells.get(ff._cell_of(6302, 702))
    far = cells.get(ff._cell_of(6300 + 16, 700))
    check("two runs, 101 s watched each (the recall out ends it)", r["runs"] == 2 and r["watched_s"] == 202.0,
          f"{r['runs']} {r['watched_s']}")
    check("our cell: watched 202 s in both radii, 2 in-view reaper deaths, 2 spawns (one per instance, same cell)",
          here == [202.0, 202.0, 2, 2], str(here))
    check("16 tiles off: watched for deaths (18) but not for spawns (14)", far is not None and far[0] == 202.0
          and far[1] == 0.0, str(far))
    check("spawn points in the field frame", sorted(map(tuple, r["spawns"])) == [(6302, 702), (6302, 702)], str(r["spawns"]))


def main():
    test_field_log()
    test_reaper_map()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

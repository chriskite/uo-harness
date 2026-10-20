"""Offline end-to-end test of the mining loop runner (harness/loop_mining.py) on test_loop_lumber's simulated
world (the rental room, the runebook, the steward, the real proxy), with mining in place of the trees: the
protocol of the live hand-mining capture 20261019_200350 (docs/NOTES.md "Live hand-mining capture").

- the pickaxe worn (layer 1); its double-click digs with no cursor: a decoy captcha gump at once, the reply
  1.0 s later, cliloc 500119 "You must wait..." for a press within 0.9 s of the last dig, the travel lockout
  ("must wait 1 seconds") on the first dig after a recall out
- two rocks by the landing (ROCK_A 4 ore, ROCK_B 6): a dig within 2 tiles of one with ore left gives 1 ore
  (cliloc 1007072; the second success "Harvest double yield/loot triggered." and 2), every third a fail
  (503043); none in reach: "You do not see any harvestable resources nearby."; each change re-sends our
  status with the weight (ore 2 stones)
- the forge at FORGE_POS: an ore stack's double-click within 2 tiles of it turns n ore into 2n ingots
  (cliloc 501988), farther "You are not near a forge" (500420)

mine_main: one trip from the rental room to the rocks by the runebook's "Sim Woods" rune; both stands dug out
(the leg runs dry), smelted at the forge on the way (weight) and at the end, home, the ingots carried in the
trapped pouch, exit 0 in the room.
mine_hop: two spots, one rock each; the first runs dry, the runner asks the plan at home (the current spot
excluded) and goes on to the second with the ingots carried (--hops 1); that one dry too, the room, exit 0.

Run: python test_loop_mining.py [mine_main|mine_hop]   (~2.5 min; private ports and temp dir, like
test_loop_lumber.py)
"""
import asyncio
import os
import sys
import time

import test_loop_lumber as T
from world.parsers import parse_packet

PICKAXE = 0x44ADB600
ORE_G, INGOT_G = 0x19B9, 0x1BF2
ORE_GS = (0x19B7, 0x19B8, 0x19B9, 0x19BA)
ROCK_A, ROCK_B = (104, 203), (95, 197)
ORE = {ROCK_A: 4, ROCK_B: 6}
STAND_A = {"x": 104, "y": 202, "n": 1, "blocks": [[ROCK_A[0] // 8, ROCK_A[1] // 8]]}
STAND_B = {"x": 96, "y": 198, "n": 1, "blocks": [[ROCK_B[0] // 8, ROCK_B[1] // 8]]}
FORGE_POS = (100, 206)
FX, FY = FORGE_POS
BASE_WEIGHT, WEIGHT_MAX = 28, 100
DOUBLE = "Harvest double yield/loot triggered."
NOTHING_NEAR = "You do not see any harvestable resources nearby."


class MineWorld(T.World):
    def __init__(self):
        super().__init__("mining")
        self.ore_left = dict(ORE)
        self.last_dig = None
        self.busy_replies = 0
        self.dig_lockouts = 0
        self.dig_n = 0                    # digs that reached a rock with ore
        self.successes = 0
        self.dug = 0                      # ore the server gave
        self.doubles = 0
        self.replies = {"success": 0, "fail": 0, "nothing_near": 0}
        self.smelted = []                 # ore per smelt
        self.smelt_refused = 0
        self.login_extra = [T.equip(PICKAXE, 0x0E86, 0x01), self.status()]

    @property
    def weight(self):
        ore = sum(n for g, n, _ in self.stacks.values() if g in ORE_GS)
        ingots = sum(n for g, n, _ in self.stacks.values() if g == INGOT_G)
        return BASE_WEIGHT + 2 * ore + ingots // 40

    def status(self):
        """Our 0x11 (the captured one) with our hits, mana and weight / weight_max (offsets checked in mine_main)."""
        pkt = bytearray(T.status_pkt(T.CHAR_NAME))
        pkt[37:39], pkt[39:41] = T.u16(self.hits), T.u16(100)
        pkt[54:56], pkt[56:58] = T.u16(self.mana), T.u16(100)
        pkt[64:66], pkt[66:68] = T.u16(self.weight), T.u16(WEIGHT_MAX)
        return bytes(pkt)

    def on_packet(self, p):
        if p[0] == 0x06:
            serial = int.from_bytes(p[1:5], "big")
            if serial == PICKAXE or (serial in self.stacks and self.stacks[serial][0] in ORE_GS):
                self.c2s.append(p)
                self.c2s_t.append(time.time())
                self.dig() if serial == PICKAXE else self.smelt(serial)
                return
        super().on_packet(p)

    def dig(self):
        now = time.time()
        if self.last_dig is not None and now - self.last_dig < 0.9:
            self.busy_replies += 1
            self.send(T.cliloc(500119))
            return
        self.last_dig = now
        if self.lockout_due:                            # set by every recall out (World.recall_to)
            self.lockout_due = False
            self.dig_lockouts += 1
            self.later(0.3, [T.sys_text("You have recently traveled and must wait 1 seconds before you may "
                                        "begin harvesting.")])
            return
        self.decoys.add(self.next_gump())
        self.send(T.gump(self.gump_serial, 0x50000000 + self.gump_serial, T.DECOY_LAYOUT,
                         ["Captcha", "Guide", "Type the Value", "Click when complete"]))
        asyncio.get_running_loop().call_later(1.0, self.dig_reply)

    def dig_reply(self):
        rock = next((r for r in ORE if self.cheb(r) <= 2 and self.ore_left[r] > 0), None)
        if rock is None:
            self.replies["nothing_near"] += 1
            self.send(T.sys_text(NOTHING_NEAR))
            return
        self.dig_n += 1
        if self.dig_n % 3 == 0:
            self.replies["fail"] += 1
            self.send(T.cliloc(503043))
            return
        self.successes += 1
        n = 2 if self.successes == 2 and self.ore_left[rock] >= 2 else 1
        self.ore_left[rock] -= n
        self.dug += n
        self.replies["success"] += 1
        if n == 2:
            self.doubles += 1
            self.send(T.sys_text(DOUBLE))
        self.send(self.stack_pkt(self.add_wood(ORE_G, n, T.BACKPACK)))
        self.send(T.cliloc(1007072))
        self.send(self.status())

    def smelt(self, serial):
        if self.cheb(FORGE_POS) > 2:
            self.smelt_refused += 1
            self.send(T.cliloc(500420))
            return
        _, n, _ = self.stacks.pop(serial)
        self.send(T.delete(serial))
        self.send(self.stack_pkt(self.add_wood(INGOT_G, 2 * n, T.BACKPACK)))
        self.send(T.cliloc(501988))
        self.send(self.status())
        self.smelted.append(n)


SPOTS_DOC = {"spots": [{"id": "sim", "name": "simulated rocks", "facet": 0,
                        "area": {"center": list(T.START), "radius": 12}, "forge": {"x": FX, "y": FY, "z": 0},
                        "stands": [STAND_A, STAND_B], "pvp": False, "status": "active"}]}


async def mine_main():
    print("\n== mine_main: dig two rocks dry, smelt at the forge, home with the ingots carried ==")
    world = MineWorld()
    st = parse_packet("s2c", world.status())
    T.check("the simulated status packet carries our weight and weight_max",
            (st.get("weight"), st.get("weight_max"), st.get("hits"), st.get("mana")) == (BASE_WEIGHT, WEIGHT_MAX, 100, 100),
            str(st))
    text, code, store, _rows = await T.run_scenario(
        world, "mine_main", [], ["--trips", "1", "--hops", "0", "--human", "off", "--smelt-free", "60",
                                 "--field-min", "30"],
        script="harness/loop_mining.py", spots_doc=SPOTS_DOC)
    try:
        T.check("runner exited 0", code == 0, str(code))
        T.check("it ended in the rental room", world.facet == T.ROOM_FACET, str(world.facet))
        first = next((i for i, p in enumerate(world.c2s) if p[0] == 0x06 and int.from_bytes(p[1:5], "big") == PICKAXE),
                     None)
        T.check("the pickaxe was double-clicked; no target (0x6C) went out after that",
                first is not None and not any(p[0] == 0x6C for p in world.c2s[first:]))
        T.check("no dig was pressed before the last one's reply (no 500119)", world.busy_replies == 0,
                str(world.busy_replies))
        T.check("the travel lockout came once and was waited out", world.dig_lockouts == 1
                and "travel lockout reported" in text, str(world.dig_lockouts))
        T.check("both rocks dug out", world.ore_left == {ROCK_A: 0, ROCK_B: 0} and world.dug == sum(ORE.values()),
                str(world.ore_left))
        T.check("smelted at the forge, never refused", len(world.smelted) >= 1 and world.smelt_refused == 0
                and sum(world.smelted) == world.dug, f"{world.smelted}, refused {world.smelt_refused}")
        rows = store.episodes("mining")
        row = rows[0] if len(rows) == 1 else {}
        T.check("one mining episode row: stored, dry, the server's ore, smelts",
                len(rows) == 1 and row.get("outcome") == "stored" and row.get("ore") == world.dug
                and row.get("dry") is True and row.get("smelts", 0) >= 1 and row.get("doubles") == world.doubles,
                str({k: row.get(k) for k in ("outcome", "ore", "dry", "smelts", "doubles", "why")}))
        T.check("it carried home no ore and every ingot", row.get("carried_end") == {"ore": 0, "ingots": 2 * world.dug},
                str(row.get("carried_end")))
        ingots = [(n, c) for g, n, c in world.stacks.values() if g == INGOT_G]
        T.check("the ingots sit in the trapped pouch", sum(n for n, _ in ingots) == 2 * world.dug
                and all(c in T.POUCHES for _, c in ingots), str(ingots))
        T.check("the run completed its one trip", "loop complete: 1 trip(s)" in text)
        events = store.job_events("mining")
        stands = [e for e in events if e["kind"] == "stand"]
        smelts = [e for e in events if e["kind"] == "smelt"]
        ends = [e["data"]["end"] for e in stands]
        T.check("a stand event per stay: two dug out, the others left for the forge; ok smelt events; nothing under "
                "lumber", ends.count("nothing_near") == 2 and set(ends) <= {"nothing_near", "smelt"}
                and len(smelts) == len(world.smelted) and all(e["data"]["ok"] for e in smelts)
                and store.job_events("lumber") == [], f"stand ends {ends}, {len(smelts)} smelts, "
                                                      f"{len(store.job_events('lumber'))} lumber")
        blocks = [tuple(b) for s in (STAND_A, STAND_B) for b in s["blocks"]]
        state = store.mine_blocks_state(0, blocks)
        T.check("every block of both stands is marked depleted",
                all((state.get(b) or {}).get("depleted_at") for b in blocks), str(state))
        n = store.con.execute("SELECT COUNT(*) FROM mine_attempts").fetchone()[0]
        T.check("a mine_attempts row per dig reply (lockout and busy not counted)", n == sum(world.replies.values()),
                f"{n} rows, replies {world.replies}")
        T.check("no harvest_attempts rows (the lumber planner's table)",
                store.con.execute("SELECT COUNT(*) FROM harvest_attempts").fetchone()[0] == 0)
    finally:
        store.close()


def spot_doc(sid, name, stands):
    return {"id": sid, "name": name, "facet": 0, "area": {"center": list(T.START), "radius": 12},
            "forge": {"x": FX, "y": FY, "z": 0}, "stands": stands, "pvp": False, "status": "active"}


async def mine_hop():
    print("\n== mine_hop: the first spot runs dry, home, on to the plan's next spot with the ingots, then home ==")
    world = MineWorld()
    doc = {"spots": [spot_doc("sim", "simulated rocks A", [STAND_A]), spot_doc("sim2", "simulated rocks B", [STAND_B])]}
    routes = {T.lumber_opt.route_key({"x": lx, "y": ly}, s): max(1, T.lumber_opt.area_dist(s["area"], (lx, ly)))
              for s in doc["spots"] for (lx, ly) in (T.BOOK_RUNE_POS, T.RUNE_POS)}
    text, code, store, _rows = await T.run_scenario(
        world, "mine_hop", [], ["--trips", "1", "--hops", "1", "--human", "off", "--smelt-free", "60",
                                "--field-min", "30"],
        script="harness/loop_mining.py", spots_doc=doc, routes=routes)
    try:
        T.check("runner exited 0 in the rental room", code == 0 and world.facet == T.ROOM_FACET,
                f"{code}, facet {world.facet}")
        rows = store.episodes("mining")
        T.check("two legs: sim hopped, sim2 stored",
                [(r.get("spot"), r.get("outcome"), r.get("leg")) for r in rows] == [("sim", "hopped", 1),
                                                                                   ("sim2", "stored", 2)],
                str([(r.get("spot"), r.get("outcome"), r.get("leg"), r.get("why")) for r in rows]))
        T.check("the hop carried leg 1's ingots", len(rows) == 2 and rows[0].get("hop", {}).get("spot") == "sim2"
                and rows[0]["hop"].get("carried") == 2 * ORE[ROCK_A], str(rows[0].get("hop") if rows else None))
        T.check("both legs ran dry; every rock dug out", all(r.get("dry") for r in rows)
                and world.ore_left == {ROCK_A: 0, ROCK_B: 0}, str(world.ore_left))
        hops = [e["data"] for e in store.job_events("mining") if e["kind"] == "hop"]
        T.check("hop events: on to sim2, then none (--hops used)",
                len(hops) == 2 and (hops[0].get("hop") or {}).get("spot") == "sim2"
                and "--hops 1 used" in (hops[1].get("why_not") or ""), str(hops))
        ingots = [(n, c) for g, n, c in world.stacks.values() if g == INGOT_G]
        T.check("all the ingots carried home in the trapped pouch", sum(n for n, _ in ingots) == 2 * world.dug
                and all(c in T.POUCHES for _, c in ingots), str(ingots))
    finally:
        store.close()


runs = [mine_main, mine_hop]


if __name__ == "__main__":
    pick = set(sys.argv[1:])
    for fn in runs:
        if not pick or fn.__name__ in pick:
            asyncio.run(T.run_async(fn))
    if T.OWN_LOGDIR and not T.FAILURES:
        import shutil
        shutil.rmtree(T.LOGDIR, ignore_errors=True)
    elif T.FAILURES:
        print(f"\nlogs kept in {T.LOGDIR}")
    print("\n" + ("ALL PASS" if not T.FAILURES else f"FAILURES: {T.FAILURES}"))
    sys.exit(0 if not T.FAILURES else 1)

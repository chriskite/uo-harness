"""Mining loop runner: dig ore at a mining spot, smelt it at the spot's forge, drop the ingots off at home.

    python harness/loop_mining.py --spot forge_1227_1142 [--trips 1] [--field-min 60] [--hops 3]
    (normally `ctl run mining ...` with the command `ctl mining plan` gives)

A LumberLoop (loop_lumber.py) with the harvesting swapped out: the trip from home (the rental
room, the rune landing, the threat checks, escapes, the speech hold, the captcha hand-off, the
way home and into the room) is the lumber runner's. What differs (protocol: the live hand-mining
capture 20261019_200350, docs/NOTES.md "Live hand-mining capture"):

- **Digging** is a stock double-click on the pickaxe in hand; no cursor follows. A pickaxe in the
  pack is double-clicked first, which only equips it (layer 1). The reply comes ~1.0 s later; a
  press sooner gets 500119 "You must wait..." (busy: retried, not counted).
- **Stands**: the area's stands from the map files (ore_nodes.area_stands: a walkable tile and the
  8x8 ore blocks within reach 2), minus blocks the store marks depleted (regrowth window
  --regrow-min) or not harvestable (mine_blocks). The next stand: most available blocks, nearer
  first (STAND_DIST_SCALE), out of every escaped creature's reach. "You do not see any harvestable
  resources nearby." marks the stand's blocks depleted; the leg runs dry when no stand is left.
- **Smelting** at the spot's natural forge (its `forge`), within forge_range tiles: a double-click
  per ore stack (501988 at once; 2 ingots an ore) when fewer than --smelt-free stones remain. With a
  forge in the rental room (homes.json `forge`, user 2026-10-20) that's all the field smelting: the
  rest of the ore rides home (across hops too) and is smelted there before the store (convert). Without
  one also before leaving the spot: at the leg's end (dry, field time up, a break or stop request) and
  before a plain abort's way home (user 2026-10-19: "just before we get full on weight or leave the
  spot"); a threat's recall goes home at once, raw ore and all. The ingots go
  into the trapped pouch and ride along across hops; at the trip's end in the rental room they go into
  the Resource Stockpile by its context menu's Restock with our backpack, which takes them out of the
  live pouch (user 2026-10-19), and then the storage shelf's Restock and Resupply run as for lumber.
  A spot forge that answers "not near a forge" disables the spot (a runner row in mining_spots).
- **A trip** digs --field-min field minutes in all, across hops: a leg that runs dry goes home and,
  with --hops left, on to the next spot mining_opt plans (greedy, the current spot excluded).
Each dig is a mine_attempts row; each stand a `stand` job event; each smelt a `smelt` job event;
each leg an episode row (loop 'mining') that mining_opt.trip_obs reads.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions  # noqa: E402
from agent_link import Abort, cheb, containers_to_open, log, serial_of  # noqa: E402
import dropoff as dropoff_mod  # noqa: E402
import escape as escape_mod  # noqa: E402
import localmap  # noqa: E402
import loop_lumber  # noqa: E402
from loop_lumber import Escape, KeepAway  # noqa: E402
import lumber_opt  # noqa: E402
import mining_opt  # noqa: E402
import ore_nodes  # noqa: E402
import shelf as shelf_mod  # noqa: E402
import stationary  # noqa: E402
import stockpile as stockpile_mod  # noqa: E402

PICKAXES = (0x0E85, 0x0E86)
# Pickaxes a trip starts with, topped up from the room's chest (hatchets_ready). Live 2026-10-20 each wore out in the
# hand mid-leg: 0x4C982C26 after ~530 digs (11:20:45), 0x4D057279 after ~1000 (12:18:24); with none left the leg
# aborted home
PICKAXES_CARRY = 2
ORE = (0x19B7, 0x19B8, 0x19B9, 0x19BA)
INGOTS = (0x1BF2,)
# Ingot hue -> metal: the materials table the lumber runner's hatchets use (harness/data/hatchets.json: RunUO's resource
# hues, avarite 1763 from community Razor scripts; Outlands' Resource Stockpile lists ingots of exactly these hues).
# Another hue is "hue N". The leg row's ingots_by_metal / stored_by_metal use these names; the Jobs page prices them
# as ingot:<metal> (user 2026-10-20: iron 30 gp ... avarite 743)
METALS = {int(m["hue"]): m["name"] for m in lumber_opt.load_hatchets()["materials"]}
LAYER_ONE_HANDED = 0x01
STAND_DIST_SCALE = 20        # steps (tiles without a map): a stand this far counts half as much as one here (next_stand)
STATIONARY_MAX = 3           # "A stationary penalty is in effect" this often at one stand: abort
# Ore stands in a row with no route to them or "Harvesting is not allowed in this area." mark the spot unworkable: it is
# disabled and the leg moves on (live 2026-10-19, forge_1696_1438: the Paladin forge inside Prevalia, whose map
# "stands" were the city walls, took ~3 s of route planning each over 434 stands). On the map only "not allowed"
# counts: a stand there is walkable (walk_steps), so what cut the way passes (live 2026-10-20 11:47, forge_1279_1149:
# a zombie in a one-tile cave passage made 8 walks fail and the spot was disabled)
UNWORKABLE_STANDS = 8
# A walk to a stand on the map may take at most STAND_ROUTE_MIN + STAND_DETOUR x its walking steps (walk_steps):
# a longer detour round creatures' zones is given up at once, not after a 30000-expansion search (~3 s)
STAND_ROUTE_MIN, STAND_DETOUR = 10, 2
# Tiles beyond the box around the area and us that the walking-distance flood (walk_steps) may cross
STAND_MARGIN = 40
BUSY_RETRY_S = 0.4           # after 500119 "You must wait to perform another action."
EQUIP_WAIT_S = 3.0           # a packed pickaxe's double-click until it is in hand
SMELT_WAIT_S = 4.0           # an ore stack's double-click until the forge's answer (live: at once)
INGOT_WAIT_S = 2.0           # after the last smelt answer: until 2 ingots an ore are in the pack (the count)
STOCKPILE_WAIT_S = 3.0       # the stockpile's answer to the Restock's backpack target (live: 46 ms, deletes first)


def metal(hue) -> str:
    return METALS.get(int(hue or 0), f"hue {int(hue or 0)}")


def by_metal(items) -> dict:
    """{metal: ingots} of [(serial, item)] ingot stacks."""
    out = {}
    for _, it in items:
        name = metal(it.get("hue"))
        out[name] = out.get(name, 0) + (it.get("amount") or 1)
    return out


def gained(after: dict, before: dict) -> dict:
    """{metal: n} that `after` holds more of than `before` (n > 0 only)."""
    return {k: after.get(k, 0) - before.get(k, 0) for k in after if after.get(k, 0) > before.get(k, 0)}


def add_to(stats: dict, key: str, counts: dict):
    """Sum `counts` ({metal: n}) into stats[key]."""
    tot = stats.setdefault(key, {})
    for k, n in counts.items():
        tot[k] = tot.get(k, 0) + n


class MineLoop(loop_lumber.LumberLoop):
    JOB = "mining"
    SKILL = "Mining"
    YIELD = INGOTS
    STASH = INGOTS
    POUCHED = INGOTS
    CARRIED = {"ore": ORE, "ingots": INGOTS}
    HOME_DONE = "ingots stored"

    def setup_tool(self, args):
        self.hatchets = lumber_opt.load_hatchets()    # lumber_opt.character's table (snapshot)
        self.want_hatchet = None
        self._ore_tiles = None       # ore_nodes.Tiles over the map's tiledata (candidate_stands)
        self._block_state = {}       # (bx, by) -> mine_blocks row, read by candidate_stands
        self._blocked_only = False   # next_stand found stands with ore, all within a creature's reach
        self.dry_blocks = set()      # this leg: blocks a stand said "nothing nearby" for
        self.no_route_stands = set()  # this leg: stands no route reached
        self.trip_field_s = 0.0      # this trip: field seconds of its finished legs (--field-min across hops)
        self.leg_t0 = None           # monotonic start of the current harvest_trip's field time
        self.unsmeltable = set()     # this leg: ore stacks the forge refused or didn't answer (not tried again)
        self._forge_failed = False   # this leg: the forge said it isn't one (smelt_leaving tries no more)
        self.fruitless = 0           # this leg: stands in a row with no route or no mining allowed (UNWORKABLE_STANDS)
        self._steps = None           # walk_steps' last flood: ((spot, x, y, z), {(x, y): steps})

    # ------------------------------------------------------------ the pickaxe
    def pickaxes(self, st) -> list:
        """[(depth, serial)] of the pickaxes we wear (depth 0) or carry at any bag depth."""
        me, pack, items = self.self_serial(st), self.backpack(st), st["world"]["items"]
        out = []
        for key, it in items.items():
            if it.get("graphic") in PICKAXES and it.get("container") is not None:
                depth = loop_lumber.pack_depth(items, serial_of(it["container"]), me, pack)
                if depth is not None:
                    out.append((depth, serial_of(key)))
        return sorted(out)

    def hatchet(self, st, want=None) -> int:
        """The pickaxe: the worn one, else the shallowest in the backpack or a bag in it."""
        found = self.pickaxes(st)
        if not found:
            raise Abort("no pickaxe worn, in the backpack or in a bag in it")
        return found[0][1]

    def hatchets_ready(self, in_room: bool):
        """Before a trip: PICKAXES_CARRY pickaxes worn or carried. Short and in the rental room, the room
        chest's pickaxes come into the pack (pickaxes_from_chest). None at all: a `low_supplies` juncture and
        no trip. The trip row's `pickaxes`."""
        n = len(self.pickaxes(self.state()))
        taken = []
        if n < PICKAXES_CARRY and in_room:
            taken = self.pickaxes_from_chest(PICKAXES_CARRY - n)
            n = len(self.pickaxes(self.state()))
        if n == 0:
            self.memory.juncture(self.JOB, "low_supplies", "No pickaxe carried or in the room's chest: no trip",
                                 "attention", {"item": "pickaxe", "have": 0, "need": 1, "trip": self.trip_n,
                                               "how": "put pickaxes into the room's chest, or a provisioner sells a "
                                                      "Pickaxe (25 gp): ctl act buy <provisioner serial> pickaxe"})
            raise Abort("no pickaxe carried or in the room's chest: no trip")
        self.pre_stats["pickaxes"] = {"have": n, "from_chest": taken}

    def pickaxes_from_chest(self, want: int) -> list:
        """Up to `want` pickaxes out of the room's chest into the pack (opened like a player would; the stock
        lift and drop), as the lumber runner takes hatchets (hatchets_from_chest). Returns the serials taken."""
        chest, where = loop_lumber.home_mod.chest_serial(self.home), tuple(self.home["chest"]["pos"][:2])
        name = self.home["chest"].get("name") or "chest"
        if self.wait_for(lambda s: self.item(s, chest) is not None, 3.0) is None:
            log(f"pickaxes: the {name} isn't in view; none taken from it")
            return []
        if cheb(self.link.pos(self.link.state()), where) > loop_lumber.CHEST_REACH:
            self.mover.walk_to(lambda: where, 1, f"to the {name}")
        self.open_for((chest, True))
        taken, tried = [], set()
        while len(taken) < want:
            st = self.link.state()
            s = next((serial_of(k) for k, it in st["world"]["items"].items()
                      if it.get("graphic") in PICKAXES and serial_of(k) not in tried
                      and serial_of(it.get("container") or "0") == chest), None)
            if s is None:
                break
            tried.add(s)
            pack = self.backpack(st)
            log(f"pickaxes: 0x{s:08X} out of the {name}")
            self.human.wait("use")
            self.link.act(actions.lift(s, 1))
            self.human.wait("drag")
            self.link.act(actions.drop(s, loop_lumber.DROP_AUTO, loop_lumber.DROP_AUTO, 0, 0, pack))
            if self.wait_for(lambda st2: s in loop_lumber.ledger_mod.pack_items(st2, pack), 4.0) is None:
                log(f"pickaxes: 0x{s:08X} did not come into the backpack")
                continue
            taken.append(f"0x{s:08X}")
        if len(taken) < want:
            log(f"pickaxes: the {name} had {len(taken)} of the {want} wanted")
        return taken

    def snapshot(self, st) -> dict:
        """Who digs this trip and with what: Mining, mount, buffs and the pickaxe."""
        ch = lumber_opt.character(st["world"], self.self_serial(st), self.hatchets)
        me = st["world"].get("self") or {}
        try:
            p = self.hatchet(st)
        except Abort:
            p = None
        it = (self.item(st, p) or {}) if p is not None else {}
        pick = None if p is None else {
            "serial": f"0x{p:08X}", "graphic": None if it.get("graphic") is None else f"0x{it['graphic']:04X}",
            "hue": it.get("hue"), "worn": loop_lumber.in_hand(st["world"], p, self.self_serial(st))}
        return {"character": {"serial": ch["serial"], "name": ch["name"]}, "skill": lumber_opt.skill_value(me, "Mining"),
                "mounted": ch["mounted"], "buffs": ch["buffs"], "pickaxe": pick}

    def leg_row(self, snap) -> dict:
        return {"pickaxe": snap.get("pickaxe")}

    def trip_done_text(self, n) -> str:
        return (f"Trip {n} done: {self.stats.get('ore', 0)} ore dug, {self.stats.get('stockpiled', 0)} ingots into the "
                f"resource stockpile")

    def pickaxe_in_hand(self, st) -> int:
        """The pickaxe in hand; a packed one is double-clicked into the hand first (the server
        equips it, live 20261019_200350), the bags on the way opened like a player."""
        me = self.self_serial(st)
        for key, it in st["world"]["items"].items():
            if it.get("graphic") in PICKAXES and it.get("container") is not None \
                    and serial_of(it["container"]) == me and it.get("layer") in (None, LAYER_ONE_HANDED):
                return serial_of(key)
        p = self.hatchet(st)
        log(f"pickaxe 0x{p:08X} isn't in hand: equipping it")
        closed = containers_to_open(st["world"], p)
        if closed:
            self.link.open_containers(closed, self.human)
        self.human.wait("dig_use")
        self.link.act(actions.dclick(p))
        if self.wait_for(lambda s: loop_lumber.in_hand(s["world"], p, me), EQUIP_WAIT_S) is None:
            raise Abort(f"pickaxe 0x{p:08X} didn't go into the hand")
        return p

    # ------------------------------------------------------------ trips
    def trip(self, n) -> bool:
        self.trip_field_s = 0.0
        return super().trip(n)

    def leg(self, n, hops: int) -> dict | None:
        self.dry_blocks, self.no_route_stands, self.leg_t0 = set(), set(), None
        self.fruitless = 0           # stands in a row with no route or no mining allowed (UNWORKABLE_STANDS)
        self.unsmeltable = set()     # ore stacks this leg's forge didn't take (refused or no answer): not again
        self._forge_failed = False
        return super().leg(n, hops)

    def convert(self):
        """In the rental room before the store: the carried ore smelted at the room's forge (room_forge; user
        2026-10-20: a small forge in the room "the runner can use to smelt ore at home"), so it is stored as ingots.
        Without one the raw ore rides along to the next trip's field forge. A forge that fails is logged; the store
        goes on."""
        ore = sum(it.get("amount") or 1 for _, it in self.in_pack(self.state(), ORE))
        if not ore:
            return
        if self.room_forge is None:
            log(f"mining: {ore} raw ore carried and no forge in the room: it rides along to a field forge")
            return
        try:
            self.smelt("home", room=True)
        except Abort as x:
            log(f"no smelt at home: {x}; the ore stays in the pack")

    def store(self) -> int:
        """The ingots into the room's Resource Stockpile (ingots_to_stockpile). Then, with none left in the pack, the
        room shelf's Restock with our backpack and Resupply, as the lumber runner does (the user's routine,
        2026-10-05). With ingots still in the pack (no stockpile, or it left some) the shelf's Restock is
        left out: it takes live trapped pouches, the one holding them too (docs/NOTES.md "Storage shelves").
        Either way the spent pouches we set off go into the chest unless the shelf took them. Returns the
        ingots stored."""
        stored = self.ingots_to_stockpile()
        st = self.state()
        if not self.count(st, INGOTS) and self.args.resupply != "off" and shelf_mod.find_shelves(st):
            self.stats["restock"] = self.resupply_here("room", restock=True)
            return stored
        spent = self.spent_pouches(st)
        if spent:
            self.to_chest([(s, {"amount": 1}) for s in spent], "spent trapped pouch")
        return stored

    def ingots_to_stockpile(self) -> int:
        """Every ingot in the pack into the home's Resource Stockpile (home.stockpile): its context menu's
        Restock (stockpile.RESTOCK_ENTRY) with the cursor answered by our backpack, which takes them out of
        the live trapped pouch too and leaves it armed (user 2026-10-19: "we can just 'restock' the resource
        stockpile and target our own backpack"; live the same day, docs/NOTES.md). The stockpile may take
        other valid items as well (its Settings), so the whole pack is declared to the ledger first. Returns
        the ingots that left the pack (`stored` and `stockpiled` of the leg row); a step the server refused
        aborts the run with the rest still carried. 0 without a stockpile or ingots."""
        pile = self.home.get("stockpile")
        st = self.state()
        before = self.count(st, INGOTS)
        before_m = by_metal(self.in_pack(st, INGOTS))
        if pile is None or not before:
            return 0
        serial, where = int(pile["serial"], 16), tuple(pile["pos"][:2])
        if self.wait_for(lambda s: self.item(s, serial) is not None, 3.0) is None:
            raise Abort(f"the resource stockpile {pile['serial']} isn't in view in the rental room")
        if cheb(self.link.pos(self.link.state()), where) > dropoff_mod.RANGE:
            self.doing("store", "Going to the resource stockpile", where)
            self.mover.walk_to(lambda: where, dropoff_mod.RANGE, "to the resource stockpile")
        self.doing("store", f"Restocking the resource stockpile with {before} ingots", where)
        for s in shelf_mod.carried(self.state()):
            self.ledger.expect(("moved_out", s))     # into our own stockpile: not theft
        plan = {"steps": [{"serial": pile["serial"], "name": "the resource stockpile",
                           "entry": stockpile_mod.RESTOCK_ENTRY, "target": "pack"}]}
        mark = len(self.link.events)
        try:
            res = dropoff_mod.run(escape_mod.LinkIO(self.link), self.human, plan)
        except dropoff_mod.DropoffError as e:
            raise Abort(f"resource stockpile: {e}")

        def answers():                                # "You add N item(s) ..." / "You do not find any items ..."
            return [t for e in self.since(mark) if e.get("ev") == "speech_heard"
                    for t in [(e.get("text") or "").strip()] if t.startswith("You ")]
        if res["ok"]:                                 # live the ingots' deletes came 46 ms after the target, then the line
            self.wait_for(lambda s: bool(answers()), STOCKPILE_WAIT_S)
        st = self.state()
        left = self.count(st, INGOTS)
        added = before - left                         # only what the server took left the pack
        stored_m = gained(before_m, by_metal(self.in_pack(st, INGOTS)))
        self.stats["stored"] = self.stats.get("stored", 0) + added
        self.stats["stockpiled"] = self.stats.get("stockpiled", 0) + added
        add_to(self.stats, "stored_by_metal", stored_m)
        log(f"added {added} ingots to the resource stockpile "
            f"({', '.join(f'{n} {k}' for k, n in sorted(stored_m.items(), key=lambda kv: -kv[1])) or 'none'}; "
            f"Restock with the backpack: {'; '.join(answers()) or 'no answer'})"
            + (f"; {left} still in the pack" if left else ""))
        if not res["ok"]:
            raise Abort(f"resource stockpile: {res['error']}; {left} ingot(s) left in the pack")
        return added

    def salvage(self, e: Abort):
        """Before a harvest abort ends the run: loose ingots into the trapped pouch (stash), unless
        stopping at once is safer (unsafe_stop). A failure is logged, not raised."""
        why = self.unsafe_stop(e)
        if why:
            log(f"stopping at once: {why}")
            return
        try:
            st = self.link.state()
            if not self.in_pack(st, INGOTS, top=True) or self.log_pouch(st) is None:
                return
            self.mode = "salvage"
            log(f"keeping the carried ingots in the trapped pouch before stopping ({e})")
            self.stash_now()
        except Abort as x:
            log(f"could not stash the carried ingots: {x}")
        finally:
            self.mode = "work"

    def remember_faction_zone(self, spot: str, zone: dict):
        row = next((r for r in self.memory.mining_spot_rows() if r["id"] == spot), None)
        self.memory.mining_spot_put(spot, row["status"] if row else "active",
                                    {**(row["data"] if row else {}), "faction_zone": zone},
                                    row["source"] if row else "runner", row["reason"] if row else None)

    def ended_early(self) -> str | None:
        """Why the leg ended before its field time with no break due (a hop may follow), or None."""
        if self.ending or self.stats.get("time_up"):
            return None
        ore = self.stats.get("ore", 0)
        if self.stats.get("dry"):
            return f"the area ran dry at {ore} ore"
        if self.stats.get("creature_blocked"):
            return f"every stand left was within a creature's reach at {ore} ore"
        if self.stats.get("unworkable"):
            return f"the spot is unworkable ({self.stats['unworkable']})"
        return None

    def unworkable(self):
        """UNWORKABLE_STANDS stands in a row had no route or allowed no mining: the spot is disabled in the store
        (a `disabled` mining_spots row; mining_opt plans it no more) and the leg ends (stats `unworkable`)."""
        spot = self.k["spot"]["id"]
        reason = (f"{self.fruitless} ore stands in a row unreachable or not minable (runner, "
                  f"{time.strftime('%Y-%m-%d %H:%M')})")
        row = next((r for r in self.memory.mining_spot_rows() if r["id"] == spot), None)
        self.memory.mining_spot_put(spot, "disabled", row["data"] if row else {}, row["source"] if row else "runner",
                                    reason)
        st = self.link.state()
        self.memory.job_event(self.JOB, "unworkable", {"spot": spot, "trip": self.trip_n, "reason": reason,
                                                       "no_route": len(self.no_route_stands)}, **self._where(st))
        self.stats["unworkable"] = reason
        log(f"spot {spot} disabled: {reason}; leaving it")

    def try_hop(self, reason: str, timed, recall: bool) -> dict | None:
        """The leg ended early (`reason`): home, then on to the spot mining_opt picks (greedy, this one
        excluded) with the ingots carried, unless storing them first is worth more (mining_opt.hop_choice: the
        load at risk in the next leg against the room's time; user 2026-10-20, as lumber stores its boards);
        None: the room as before (the ingots into the stockpile, the trip over). A `hop` job event either way."""
        st = self.link.state()
        here = self.k["spot"]["id"]
        why_not = self.hop_blocked(st)
        if why_not is None and recall:
            try:
                timed("hop_home", self.go_home, retry=False)
            except Abort as x:
                why_not = f"recall home: {x}"
        if why_not is not None:
            self.memory.job_event(self.JOB, "hop", {"trip": self.trip_n, "from": here, "reason": reason,
                                                    "why_not": why_not}, **self._where(st))
            log(f"no hop ({reason}): {why_not}")
            return None
        self.settle_at_home()
        mode, self.mode = self.mode, "salvage"
        try:
            st = self.link.state()
            ingots = self.count(st, INGOTS)
            self.doing("hop_plan", f"Home with {ingots} ingots: another spot, or the room?")
            out = mining_opt.plan_from_store(self.memory, st["world"], self.self_serial(st), self.link.pos(st),
                                             self.facet_now(st), seeds_path=self.args.spots, exploit=True,
                                             exclude={here}, home=self.home)
        finally:
            self.mode = mode
        st = self.link.state()
        pick = out.get("pick")
        why_not = self.hop_blocked(st) or (None if pick else out.get("error"))
        risk = None
        ore = self.count(st, ORE)
        if why_not is None:
            leg_h = max(0.0, self.args.field_min * 60 - self.trip_field_s) / 3600.0
            # only what the room would take counts: the ingots, and the raw ore too when the room's forge smelts it
            # (room_forge); without one it rides along either way (live 2026-10-20 13:11:55 the room was chosen to
            # "store the 0 ingots" over 21 ore of raw-ore risk)
            at_risk = self.count(st, INGOTS) / 2 + (ore if self.room_forge is not None else 0)
            go, risk = mining_opt.hop_choice(pick, at_risk, leg_h)
            if not go:
                why_not = (f"storing the {ingots} ingots{f' and {ore} ore' if ore else ''} first is worth more (a hop "
                           f"to {pick['spot']} risks {risk['risk_ore']} ore, the room costs {risk['store_cost_ore']})")
        if why_not is not None:
            self.memory.job_event(self.JOB, "hop", {"trip": self.trip_n, "from": here, "reason": reason,
                                                    "why_not": why_not, "risk": risk}, **self._where(st))
            log(f"no hop ({reason}): {why_not}")
            return None
        self.settle_at_home()
        self.memory.job_event(self.JOB, "hop", {"trip": self.trip_n, "from": here, "reason": reason, "carried": ingots,
                                                "carried_ore": ore, "hop": pick, "risk": risk}, **self._where(st))
        log(f"hop ({reason}): on to {pick['spot']} with {ingots} ingots{f' and {ore} ore' if ore else ''} (risks "
            f"{risk['risk_ore']} ore; the room would cost {risk['store_cost_ore']})")
        return {"spot": pick["spot"], "why": reason, "carried": ingots, "carried_ore": ore, "mode": "exploit",
                "logs_per_trip": None, "gain_logs": None}

    def hop_to(self, hop: dict):
        """The run's spot becomes the hop's (mining_opt.spot_knowledge); per-spot threat memory starts over.
        The trip's field minutes (--field-min) go on across hops."""
        spot = mining_opt.load_spots(self.memory, self.args.spots)[hop["spot"]]
        self.k = mining_opt.spot_knowledge(self.know_base, spot)
        self.facet = self.k["facet"]
        self.out_landing = None
        self.afield = False
        self.escaped, self.counted, self.sighted = set(), set(), {}
        self.faction_first, self.faction_watched, self.waypost_seen = {}, set(), False
        self.doing("hop", f"Going on to {spot.get('name') or hop['spot']} with {hop['carried']} ingots")

    # ------------------------------------------------------------ digging
    def outcome(self, mark):
        """The server's answer to a dig since events[mark] (mining.json `harvest`): success (1 ore, 2 after
        "Harvest double yield/loot triggered."), fail, nothing_near, no_harvest, lockout (seconds),
        stationary, busy, mounted; None while none came."""
        hv, lock = self.k["harvest"], self.k["travel_lockout"]
        double = False
        for ev in self.since(mark):
            e = ev.get("ev")
            if e == "speech_heard":
                t = ev.get("text") or ""
                if t == hv["double_text"]:
                    double = True
                elif t in hv["nothing_near_texts"]:
                    return ("nothing_near", 0)
                elif t in hv["no_harvest_texts"]:
                    return ("no_harvest", 0)
                elif t.startswith(lock["text_prefix"]):
                    digits = [int(w) for w in t.split() if w.isdigit()]
                    return ("lockout", digits[0] if digits else lock["seconds"])
                elif t.startswith(hv["stationary_text_prefix"]):
                    return ("stationary", 0)
            elif e == "cliloc":
                n = ev.get("cliloc")
                if n in hv["success_clilocs"]:
                    return ("success", 2 if double else 1)
                if n in hv["fail_clilocs"]:
                    return ("fail", 0)
                if n in hv["nothing_near_clilocs"]:
                    return ("nothing_near", 0)
                if n in hv["no_harvest_clilocs"]:
                    return ("no_harvest", 0)
                if n in hv["busy_clilocs"]:
                    return ("busy", 0)
                if n in hv["mounted_clilocs"]:
                    return ("mounted", 0)
        return None

    def attempt(self):
        """One dig: the pickaxe in hand double-clicked at a script's pace (dig_use), the reply read
        (outcome) -> (outcome, ore gained); ("none", 0) when no reply came within --attempt-timeout."""
        st = self.state()
        before = self.count(st, ORE)
        self.self_care(st)
        self._acting = True                          # no potion between the dig and its reply
        try:
            pick = self.pickaxe_in_hand(st)
            self.human.wait("dig_use")
            mark = len(self.link.events)
            self.link.act(actions.dclick(pick))
            end = time.monotonic() + self.args.attempt_timeout
            while time.monotonic() < end:
                self.state()
                cap = self.real_captcha(mark)
                if cap is not None:
                    self.captcha_handoff(cap)
                    mark = cap + 1                   # the server finishes the dig after the answer
                    end = time.monotonic() + self.args.attempt_timeout
                    continue
                out = self.outcome(mark)
                if out is not None:
                    kind, n = out
                    if kind != "success":
                        return out
                    st = self.wait_for(lambda s: self.count(s, ORE) > before, 3.0)
                    gained = max(self.count(st or self.link.state(), ORE) - before, 0)
                    return (kind, gained or n)
                time.sleep(0.1)
            return ("none", 0)
        finally:
            self._acting = False

    # ------------------------------------------------------------ smelting
    def smeltable(self, st) -> list:
        """[(serial, item)] of the ore stacks carried that this leg's forge hasn't failed on."""
        return [(s, it) for s, it in self.in_pack(st, ORE) if s not in self.unsmeltable]

    def smeltable_ore(self, st) -> int:
        return sum(it.get("amount") or 1 for _, it in self.smeltable(st))

    def needs_smelt(self, st) -> bool:
        """Smeltable ore carried and fewer than --smelt-free stones left (smelt mid-stand)."""
        me = st["world"].get("self") or {}
        wmax, w = (me.get("stats") or {}).get("weight_max"), me.get("weight")
        return wmax is not None and w is not None and self.smeltable_ore(st) > 0 and wmax - w < self.args.smelt_free

    def smelt_reply(self, mark) -> tuple | None:
        """('ok' | 'refused' | 'not_near', 0) or ('lockout', seconds) from the answer since events[mark], or None."""
        sm, lock = self.k["smelt"], self.k["travel_lockout"]
        for ev in self.since(mark):
            if ev.get("ev") == "cliloc":
                n = ev.get("cliloc")
                if n == sm["ok_cliloc"]:
                    return ("ok", 0)
                if n in sm["refused_clilocs"]:
                    return ("refused", 0)
                if n in sm["not_near_clilocs"]:
                    return ("not_near", 0)
            elif ev.get("ev") == "speech_heard":
                t = ev.get("text") or ""
                if any(t.startswith(x) for x in sm["not_near_texts"]):
                    return ("not_near", 0)
                if t.startswith(lock["text_prefix"]):     # [INFERENCE] the travel lockout may hold smelting too
                    digits = [int(w) for w in t.split() if w.isdigit()]
                    return ("lockout", digits[0] if digits else lock["seconds"])
        return None

    @property
    def room_forge(self) -> dict | None:
        """The rental room's forge (homes.json `forge`: serial, pos), or None. With one the carried ore is smelted at
        home (convert) and the field forge only when the weight calls for it."""
        return (self.home or {}).get("forge")

    def smelt(self, why: str, room: bool = False) -> int:
        """Walk to the forge and double-click each ore stack (smelt_use pace). The spot's forge: the stacks
        it hasn't failed on this leg, the ingots into the trapped pouch (stash); a forge that says it isn't one
        disables the spot (Abort). `room`: the rental room's forge (room_forge), every ore stack carried, the
        ingots left loose for the stockpile; a forge that isn't one aborts and disables nothing. A `smelt` job
        event (`at` 'spot' or 'room'); returns the ingots made."""
        st = self.state()
        stacks = self.in_pack(st, ORE) if room else self.smeltable(st)
        if not stacks:
            return 0
        if room:
            forge = self.room_forge
            fserial = int(forge["serial"], 16)
            if self.wait_for(lambda s: self.item(s, fserial) is not None, 3.0) is None:
                raise Abort(f"the room's forge {forge['serial']} isn't in view in the rental room")
            fx, fy = forge["pos"][:2]
            where = "the room's forge"
        else:
            fx, fy = self.k["forge"]["x"], self.k["forge"]["y"]
            where = f"the forge at {fx},{fy}"
        sm = self.k["smelt"]
        ore0, ingots0 = sum(it.get("amount") or 1 for _, it in stacks), self.count(st, INGOTS)
        metals0 = by_metal(self.in_pack(st, INGOTS))
        t0 = time.monotonic()
        self.doing("to_forge", f"To {where} to smelt {ore0} ore", (fx, fy))
        self.mover.walk_to(lambda: (fx, fy), sm["forge_range"], "to the forge")
        self.doing("smelt", f"Smelting {ore0} ore at {where}", (fx, fy))
        spot = self.k["spot"]["id"]
        for serial, it in stacks:
            for _try in range(2):                       # a second press only after a travel lockout
                st = self.state()
                if self.item(st, serial) is None:
                    break
                closed = containers_to_open(st["world"], serial)
                if closed:
                    self.link.open_containers(closed, self.human)
                self.human.wait("smelt_use")
                mark = len(self.link.events)
                self.ledger.expect(("consumed", serial))
                self.link.act(actions.dclick(serial))
                self.wait_for(lambda s, mark=mark: self.smelt_reply(mark) is not None, SMELT_WAIT_S)
                reply, secs = self.smelt_reply(mark) or (None, 0)
                if reply == "lockout" and _try == 0:
                    wait = secs + self.human.rng.uniform(1.0, 3.0)
                    log(f"smelt: travel lockout reported: waiting {wait:.0f} s")
                    self.doing("lockout", f"Waiting out the travel lockout ({wait:.0f} s)", (fx, fy))
                    self.pause(wait, "lockout")
                    self.timing["lockout_s"] += wait
                    continue
                if reply == "ok":
                    break
                if reply == "not_near":
                    at = "room" if room else "spot"
                    reason = f"forge ({fx},{fy}) doesn't smelt (runner, {time.strftime('%Y-%m-%d %H:%M')})"
                    if not room:
                        row = next((r for r in self.memory.mining_spot_rows() if r["id"] == spot), None)
                        self.memory.mining_spot_put(spot, "disabled", row["data"] if row else {},
                                                    row["source"] if row else "runner", reason)
                        self._forge_failed = True
                    self.memory.job_event(self.JOB, "smelt", {"why": why, "at": at, "forge": [fx, fy], "ok": False,
                                                              "trip": self.trip_n, "spot": spot, "reason": reason},
                                          **self._where(self.link.state()))
                    raise Abort(f"{where} doesn't smelt" + ("" if room else ": spot disabled"))
                self.unsmeltable.add(serial)            # refused or no answer: not this leg again (no smelt loop)
                log(f"smelt: {'the forge refused' if reply == 'refused' else 'no answer to'} "
                    f"{it.get('amount') or 1} ore (0x{serial:08X}); leaving that stack this leg")
                break
        st = self.state()
        tried = {s for s, _ in stacks}
        ore = ore0 - sum(it.get("amount") or 1 for s, it in self.in_pack(st, ORE) if s in tried)
        if ore:      # live 2026-10-20 11:42:13: "109 ore into 0 ingots", then 218 stashed: they came after the answer
            st = self.wait_for(lambda s: self.count(s, INGOTS) >= ingots0 + 2 * ore, INGOT_WAIT_S) or self.state()
        made = self.count(st, INGOTS) - ingots0
        made_m = gained(by_metal(self.in_pack(st, INGOTS)), metals0)
        dt = time.monotonic() - t0
        self.stats["smelts"] = self.stats.get("smelts", 0) + 1
        self.stats["ore_smelted"] = self.stats.get("ore_smelted", 0) + ore
        self.stats["ingots_made"] = self.stats.get("ingots_made", 0) + made
        add_to(self.stats, "ingots_by_metal", made_m)
        self.timing["smelt_s"] = self.timing.get("smelt_s", 0.0) + dt
        self.memory.job_event(self.JOB, "smelt", {"why": why, "at": "room" if room else "spot", "forge": [fx, fy],
                                                  "stacks": len(stacks), "ore": ore, "ingots": made, "metals": made_m,
                                                  "s": round(dt, 1), "trip": self.trip_n, "spot": spot, "ok": True},
                                          **self._where(st))
        log(f"smelted {ore} ore into {made} ingots at {where} ({why})")
        if not room:                                    # at home they go into the stockpile next
            self.stash()
        return made

    def smelt_leaving(self, e: Abort):
        """A plain abort takes us away from the spot (home_on_abort's recall): smelt the carried ore first.
        Not when stopping at once is safer (unsafe_stop: a player or red, death, a captcha restriction, the
        gate, possible staff, a speech hold), when we aren't out at the spot, when the forge itself failed,
        or when the room has a forge (room_forge: the ore is smelted at home, no walk to the field forge with
        the abort's cause around). A failure is logged; the abort goes on either way."""
        if not self.afield or self._forge_failed or self.room_forge is not None or self.unsafe_stop(e) \
                or not self.smeltable_ore(self.link.state()):
            return
        try:
            self.smelt("leaving")
        except (Abort, Escape, KeepAway) as x:
            log(f"no smelt before leaving ({e}): {x}")

    # ------------------------------------------------------------ stands
    def candidate_stands(self, st) -> list:
        """The area's stands (ore_nodes.area_stands on the map; the spot's `stands` without one) with
        at least one available block (avail). On the map only the stands the walk rules reach from where we
        stand (walk_steps): area_stands ignores z, and over a cave the mountain's surface counts as ground
        (live 2026-10-19, forge_2599_620: a cave of 'cave floor' statics at z 0 under 'snow' land at z 65; 647
        of its 2357 stands lay on the unreachable surface, each "no route" after a ~3 s search)."""
        area = self.k["harvest"]["area"]
        walk = self.mover.walk_map(st)
        if walk is not None:
            self._ore_tiles = self._ore_tiles or ore_nodes.Tiles(walk.m.tiledata)
            rects, _, _ = ore_nodes.exclusions(self.facet, self.args.memory)
            reached = self.walk_steps(st)
            every = ore_nodes.area_stands(walk.m, self._ore_tiles, rects, area["center"], area["radius"])
            stands = [s for s in every if (s["x"], s["y"]) in reached]
            if len(stands) < len(every):
                log(f"{len(every) - len(stands)} of {len(every)} map stands at {self.k['spot']['id']} can't be walked "
                    f"to from here (another level, walled off)")
        else:
            stands = [dict(s) for s in self.k["harvest"]["stands"]]
        for s in stands:
            s["blocks"] = [tuple(b) for b in s["blocks"]]
        self._block_state = self.memory.mine_blocks_state(self.facet, {b for s in stands for b in s["blocks"]})
        out = [s for s in stands if self.avail(s)]
        log(f"{len(out)} of {len(stands)} stands at {self.k['spot']['id']} reach ore not dug out lately")
        return out

    def walk_steps(self, st) -> dict | None:
        """{(x, y): the fewest steps from where we stand} by the walk rules (localmap.steps: z-aware, mobiles
        and danger zones aside), within STAND_MARGIN of the box around the spot's area and us; None without a
        map. Kept while we stand on the same tile at the same spot (a no-route stand, a dry one at once)."""
        x, y, z = (int(v) for v in self.link.pos(st)[:3])
        key = (self.k["spot"]["id"], x, y, z)
        if self._steps is not None and self._steps[0] == key:
            return self._steps[1]
        walk = self.mover.walk_map(st)
        if walk is None:
            return None
        area = self.k["harvest"]["area"]
        (cx, cy), r = area["center"], area["radius"] + ore_nodes.REACH
        box = (min(x, cx - r) - STAND_MARGIN, min(y, cy - r) - STAND_MARGIN,
               max(x, cx + r) + STAND_MARGIN, max(y, cy + r) + STAND_MARGIN)
        out = localmap.steps(walk, (x, y, z), box=box)
        self._steps = (key, out)
        return out

    def avail(self, stand) -> list:
        """The stand's blocks with ore as far as we know: not ruled out (no_harvest), not dry this leg,
        not depleted within --regrow-min."""
        since = time.time() - self.args.regrow_min * 60
        out = []
        for b in stand["blocks"]:
            s = self._block_state.get(b)
            if b in self.dry_blocks or (s is not None and (s["no_harvest"] or (s["depleted_at"] or 0) > since)):
                continue
            out.append(b)
        return out

    def next_stand(self, stands: list) -> dict | None:
        """The stand to dig at next: available blocks per walking distance (walk_steps; Chebyshev without a
        map; STAND_DIST_SCALE), human noise, out of every escaped creature's reach and of the aggro zone of
        every creature in view (the Mover's ("seen", serial) zones); None when none is (_blocked_only: some had
        ore but were in reach). On the map a stand the walk from here doesn't reach is skipped (live
        2026-10-19, forge_2599_620: the nearest stand by Chebyshev, 51 tiles off the landing, was 137 steps
        round the cave). Live 2026-10-20 11:47, forge_1279_1149: a zombie stood among the next stands in a
        cave; each walk there cost a 3 s search that found no route through its zone."""
        st = self.link.state()
        here = tuple(self.link.pos(st)[:2])
        far = self.walk_steps(st)
        seen = [z for k, z in self.mover.danger.items() if isinstance(k, tuple) and k[0] == "seen"]
        best, any_avail = None, False
        for s in stands:
            xy = (s["x"], s["y"])
            if xy in self.no_route_stands or (far is not None and xy not in far):
                continue
            free = self.avail(s)
            if not free:
                continue
            any_avail = True
            if not self.out_of_reach(s["x"], s["y"]) or any(cheb(xy, c) <= r for c, r in seen):
                continue
            dist = cheb(here, xy) if far is None else far[xy]
            score = len(free) / (1 + dist / STAND_DIST_SCALE) * self.human.rng.uniform(1.0, 1.15)
            if best is None or score > best[0]:
                best = (score, s)
        self._blocked_only = best is None and any_avail
        return None if best is None else best[1]

    def field_used_s(self) -> float:
        return self.trip_field_s + (time.monotonic() - self.leg_t0 if self.leg_t0 else 0.0)

    def time_up(self) -> bool:
        return self.field_used_s() >= self.args.field_min * 60

    def harvest_trip(self) -> int:
        """Dig at stands until --field-min (across the trip's hops), the stands run dry, or every stand
        left is within a creature's reach; smelt when the weight calls for it and before leaving the spot (the
        leg's end, a break or stop, a plain abort: smelt_leaving), the latter only without a room forge
        (room_forge: the ore rides home or on to the next spot, smelted in the room). A monster escape or a
        suspected thief leaves the stand, digging goes on at the next one out of reach."""
        tally = {"ore": 0, "attempts": 0, "successes": 0, "doubles": 0, "unknown": 0}
        try:
            if self.ending:
                log(f"{self.ending}: no digging this trip")
                return 0
            self.aspect_ensure("heading out")
            self.ward_ensure("heading out")
            self.guard_route_ready()
            self.go_out()
            self.afield = True
            self.track_ensure("at the spot")
            self.leg_t0 = self.leg_t0 or time.monotonic()
            stands = self.candidate_stands(self.state())
            if not stands:
                self.stats["dry"] = True
                raise Abort("no ore stand with an available node at the spot (all depleted, ruled out or no map)")
            while not self.ending:
                if self.time_up():
                    self.stats["time_up"] = True
                    log(f"field time up ({self.args.field_min:g} min) at {tally['ore']} ore")
                    break
                try:
                    if self.needs_smelt(self.state()):
                        self.smelt("weight")
                        continue
                    stand = self.next_stand(stands)
                    if stand is None:
                        if self._blocked_only:
                            self.stats["creature_blocked"] = True
                            log(f"every stand left is within a creature's reach; ending at {tally['ore']} ore")
                        else:
                            self.stats["dry"] = True
                            log(f"the area ran dry at {tally['ore']} ore (every stand dug out or unreachable)")
                        break
                    self.dig_stand(stand, tally)
                    if self.fruitless >= UNWORKABLE_STANDS:
                        self.unworkable()
                        break
                except Escape as e:
                    self.escape(e)
                except KeepAway as e:
                    self.keep_away(e)
            # leaving the spot, a break or stop request too; with a room forge the ore is smelted at home
            if self.room_forge is None and self.smeltable_ore(self.state()) > 0:
                try:
                    self.smelt("leg end")
                except Escape as e:
                    self.escape(e)
                    self.smelt("leg end")
                except KeepAway as e:
                    self.keep_away(e)
                    self.smelt("leg end")
            return tally["ore"]
        except Abort as e:
            self.smelt_leaving(e)
            raise
        finally:
            self.harvesting = False
            for k in ("attempts", "successes", "ore", "doubles"):
                self.stats[k] = self.stats.get(k, 0) + tally[k]
            if self.leg_t0:
                self.trip_field_s += time.monotonic() - self.leg_t0
                self.leg_t0 = None

    def dig_stand(self, stand: dict, tally: dict):
        """Walk onto `stand` and dig until the server says nothing in reach has ore, the field time is up,
        the pack calls for the forge, a break is due or --max-attempts-per-stand. A `stand` job event."""
        x, y = stand["x"], stand["y"]
        self.doing("to_stand", f"Heading to the rock at {x},{y} ({tally['ore']} ore)", (x, y))
        n = (self._steps[1] if self._steps is not None else {}).get((x, y))   # walk_steps from here (next_stand's)
        try:
            # on the map a detour round creatures' zones longer than STAND_DETOUR x the way is given up quickly
            self.mover.walk_to(lambda: (x, y), 0, f"to the ore stand {x},{y}",
                               max_route=None if n is None else STAND_ROUTE_MIN + STAND_DETOUR * n)
        except Abort as e:
            if "no route" not in str(e):
                raise
            self.no_route_stands.add((x, y))
            if n is None:            # on the map the stand is walkable: what cut the way (creatures, a player) passes
                self.fruitless += 1
            log(f"ore stand {x},{y}: no route; trying the next one")
            return
        z = self.link.pos(self.link.state())[2]
        rec = {"trip": self.trip_n, "spot": self.k["spot"]["id"], "stand": [x, y, z],
               "blocks": [list(b) for b in stand["blocks"]], "attempts": 0, "successes": 0, "ore": 0, "end": None}
        where = f"stand {x},{y}"
        t_stand = time.monotonic()
        self.harvesting = True       # the keep-away is on while we stand and dig
        stationary_n = 0
        try:
            while rec["attempts"] < self.args.max_attempts_per_stand and not self.ending and not self.time_up():
                self.track_ensure("between digs")
                if self.unstick((x, y)):
                    continue
                if self.needs_smelt(self.state()):
                    rec["end"] = "smelt"
                    return
                self.doing("dig", f"Mining by {x},{y} ({tally['ore']} ore)", (x, y))
                if self.timing.get("walk_out_s") is None:
                    self.timing["walk_out_s"] = time.time() - self.trip_t0
                c0, w0 = time.monotonic(), self.stats.get("speech_wait_s", 0.0)
                out, n = self.attempt()
                self.chopped(c0, w0)
                if out != "none":
                    tally["unknown"] = 0
                if out in ("success", "fail"):
                    self.fruitless = 0
                    rec["attempts"] += 1
                    tally["attempts"] += 1
                    if out == "success":
                        rec["successes"] += 1
                        rec["ore"] += n
                        tally["successes"] += 1
                        tally["ore"] += n
                        tally["doubles"] += n >= 2
                    self.memory.mine_record(self.facet, x, y, z, out, n)
                    log(f"{where}: {f'+{n} ore' if out == 'success' else 'fail'} ({tally['ore']} this leg)")
                elif out in ("nothing_near", "no_harvest"):
                    blocks = stand["blocks"]
                    self.memory.mine_blocks_mark(self.facet, blocks, "depleted" if out == "nothing_near" else out)
                    self.fruitless = self.fruitless + 1 if out == "no_harvest" else 0
                    self.dry_blocks.update(blocks)
                    self.memory.mine_record(self.facet, x, y, z, out, 0)
                    rec["end"] = out
                    log(f"{where}: {'nothing in reach has ore' if out == 'nothing_near' else 'no mining allowed'} "
                        f"after {rec['attempts']} dig(s), {rec['ore']} ore; {len(blocks)} block(s) marked; next stand")
                    return
                elif out == "lockout":
                    wait = n + self.human.rng.uniform(1.0, 3.0)
                    log(f"travel lockout reported: waiting {wait:.0f} s")
                    self.doing("lockout", f"Waiting out the travel lockout ({wait:.0f} s)", (x, y))
                    self.pause(wait, "lockout")
                    self.timing["lockout_s"] += wait     # travel's cost, not the field's (mining_opt.trip_obs)
                elif out == "busy":
                    time.sleep(BUSY_RETRY_S)
                elif out == "stationary":
                    stationary_n += 1
                    if stationary_n > STATIONARY_MAX:
                        raise Abort(f"the stationary penalty won't clear at {x},{y}")
                    if not self.unstick((x, y)):     # the server's word without the buff in view: walk anyway
                        log(f"{where}: the server reports the stationary penalty; walking it off")
                        self.still.walk_off((x, y), stationary.CLEAR_STEPS + stationary.MARGIN, "stationary penalty")
                        self.stats["stationary_clears"] = self.stats.get("stationary_clears", 0) + 1
                elif out == "mounted":
                    raise Abort("the server refuses mining while riding (cliloc 501864)")
                elif out == "none":
                    tally["unknown"] += 1
                    log(f"{where}: no recognised outcome ({tally['unknown']} in a row)")
                    if tally["unknown"] > 3:
                        raise Abort("dig attempts keep ending without a known outcome")
            rec["end"] = ("break" if self.break_due else "stop" if self.finish_requested
                          else "time_up" if self.time_up() else "max_attempts")
        except BaseException as e:
            rec["end"] = rec["end"] or f"interrupted: {type(e).__name__}: {e}"[:200]
            raise
        finally:
            self.harvesting = False
            rec["s"] = round(time.monotonic() - t_stand, 1)
            self.memory.job_event(self.JOB, "stand", rec, facet=self.facet, x=x, y=y)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    loop_lumber.common_args(ap)
    ap.add_argument("--spots", default=mining_opt.SEEDS, help="seed spot file (tests)")
    ap.add_argument("--field-min", type=float, default=60.0, help="field minutes per trip across hops")
    ap.add_argument("--smelt-free", type=float, default=40.0,
                    help="smelt mid-stand when fewer free stones remain")
    ap.add_argument("--regrow-min", type=float, default=mining_opt.REGROW_MIN,
                    help="skip an ore block for this long after a stand reaching it said nothing nearby")
    ap.add_argument("--loop", default=os.path.join(loop_lumber.DATA, "loops", "mining.json"),
                    help="common mining knowledge (texts, clilocs, captcha); the spot adds area and forge")
    ap.set_defaults(max_attempts_per_stand=120, attempt_timeout=5.0)
    loop_lumber.run_loop(ap.parse_args(), MineLoop, mining_opt.load_spots, mining_opt.spot_knowledge)


if __name__ == "__main__":
    main()

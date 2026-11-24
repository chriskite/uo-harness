"""The Hunting Grounds simulator (docs/SIM.md "Hunting Grounds simulator"): the deer event's server, in process, for
the Hunting Grounds runner (loop_hg.py) on virtual time.

A sibling of sim.world.World with the same face to sim.link.SimLink (`player`, `login_packets`, `drain`, `c2s`,
`run_until`, `_ev`, `intent`): it takes the client's packets and answers in the live protocol, so the runner sees
through the real client model (world.runtime) what it sees live, and hunting_grounds.GroundsLog reads the same lines
and gumps. Geometry is the install's map (pathfind.Walkers, facet 0: Prevalia and both instances).

Modelled (every number from harness/data/sim/hunting_grounds.json, measured on Logan Wolf's runs of 2026-11-22; the
guesses are marked [INFERENCE] here or there):
- Prevalia: the player with a stack of Hunting Permits, the Hunting Official and his menu; "Enter Hunting Grounds"
  spends a permit and lands us in the next instance (east and west alternate), the 15-minute session starts.
- The session: the clock lines at 15, 10 and 5 minutes, the expiry (held trophies lost, out to a Prevalia tile, hidden),
  the exit moongate (stepping on it ends the session), the wind's line every 30 s, the hotbar every 5 s.
- Hide (the skill or the hotbar) and stealth: always hidden at once, revealed by a running step, a shot or a step onto a
  deer; Track (a line only); Shoot (a double-click on a deer, or the hotbar's cursor) with its refusals, the ammo, the
  flight, hit by the deer's mood, graze by the weapon tier; Carve with its delay, reach and the 5-trophy cap; the
  warden's turn-in, its score and weapon tiers.
- Deer: a population by depth (density_by_band), points by depth (points_by_band), moods calm / nervous / fleeing with
  the measured per-second hazards by distance and our hidden state, the wind's penalty, recovery after calm_after_s
  unsensed, movement in bursts at the measured rates, labels every 4-6 s, respawn.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass

import combat
import hunting_grounds as hg
import los
import pathfind
from agent_link import HELD_END_TEXT
from sim import packets as P

TICK_S = 0.1
VIEW = hg.VIEW_R              # every buck came into view at exactly 18 tiles
NPC_R = 3                     # the official's and the warden's double-click reach [INFERENCE]
FAR_CLILOC = 500446           # "That is too far away." [INFERENCE: Outlands' answer to a reach refusal here]
SENSE_VISIBLE_R = 20          # '*flees*': all 379 within 20 tiles (docs/research/HUNTING_GROUNDS.md §2)
SENSE_HIDDEN_R = hg.NERVOUS_R # '*looks nervous*': 17 of 18 within 10
MOOD_S = 0.5                  # the hazards (per second in the scenario) are drawn this often
ACTIVE_R = 40                 # deer farther from the player stand still (speed): beyond the view and every hazard
DEER_WALK_S = 0.4             # a calm or nervous deer's step [INFERENCE: an animal's walk]
BURST = (2, 7)                # steps in one burst of movement [INFERENCE: "moves sporadically / frequently"]
TURN_WANDER = ((0, 0.6), (1, 0.3), (2, 0.1))   # a wandering deer's heading change per burst [INFERENCE]
TURN_FLEE = ((0, 0.4), (1, 0.4), (2, 0.2))     # off the line away from the player per step [INFERENCE: puts the net
#                                                 displacement near the measured 9.59 of 15.27 tiles a 5 s]
HOME_R = 12                   # a wandering deer heads back toward its spawn beyond this [INFERENCE: holds the density]
FLEE_FROM_R = 30              # a fleeing deer runs from the player within this, else on along its heading
UNHINDER_AFTER_SHOT_S = 0.2   # 'You are no longer hindered.' 0.2 s after the kill (timing.hindered_note)
FAST_SLACK = 0.9              # a step sooner than this share of its pace counts in metrics `fast_steps`
LABEL_HUE = 1256              # a buck's overhead lines (harness/test_hunting_grounds.py's live fixture)
PLAYER_SERIAL = 0x00104A60
PLAYER_NAME = "Logan Wolf"
HUMAN_BODY, DEER_BODY = 400, 234
BACKPACK, PERMIT = 0x40000100, 0x40000101
OFFICIAL_SERIAL = 0x00200001
GATE_GRAPHIC, GATE_HUE = 3948, 1196   # the exit moongate (gate_note)
GUMP_SERIAL0 = 0x01A2B300
# hotbar art: a held trophy's tilepic is 63990 + points / 2 (live 8 -> 63994, 10 -> 63995, 12 -> 63996; the rest
# [INFERENCE]); its x per art as live; the five rows' y; the weapon art's spot (live 5041, 30995, 30990; the rest
# [INFERENCE]); a pressed-looking button (9721) on Hide while hidden and on Shoot while its cooldown runs [INFERENCE:
# live 9721 on Hide in both five-held hotbars, on Shoot in one]; the weathervane's arrow 71774 + octant [INFERENCE:
# live 71775 and 71777, their winds unknown]
TROPHY_ART0 = 63990
TROPHY_X = {63994: 44, 63995: 38, 63996: 37}
TROPHY_Y = (89, 123, 158, 193, 229)
TROPHY_TEXT_Y = (100, 135, 170, 205, 240)
WEAPON_TILE = {5041: (49, 413), 30995: (47, 409), 30990: (44, 404)}
BTN_UP, BTN_ON = (2151, 2154), (9721, 9724)
WIND_ART0 = 71774

MINUTES_TEXT = "You now have {} minutes left remaining in this realm."
TEXT_NO_PERMIT = "You need a Hunting Permit to enter."    # [INFERENCE: the live refusal is unrecorded]
TEXT_FOLLOWERS = "Players are not allowed to bring followers into this event."
TEXT_HIDE = "You hide and may now move in stealth."
TEXT_HIDE_WAIT = "You must wait a moment before hiding again."
TEXT_TRACK = "You begin following deer tracks."            # [INFERENCE]
TEXT_TRACK_WAIT = "You must wait a moment before tracking again."   # [INFERENCE: the 5 s cooldown's line]
TEXT_SHOOT_PROMPT = "Select a target to fire at."
EXIT_GATE_AGAIN = "Click again to confirm exiting the Hunting Grounds. This will end your current encounter."  # live
TEXT_STATIONARY = "You must remain stationary for a moment before shooting."
TEXT_SHOT_WAIT = "You must wait a few moments before shooting again."
TEXT_RANGE = "That is out of range of your weapon."
TEXT_HOLD_FULL = ("You are already holding 5 trophies. Return to the lodge to turn them in before shooting "
                  "additional animals.")
TEXT_NOT_DEER = "You cannot shoot that."
TEXT_NO_AMMO = "You are out of ammunition. Return to the game warden to resupply."   # [INFERENCE: never seen live]
TEXT_FELLED = "You have felled a deer! Carve it to claim a trophy."
TEXT_CARVE_WAIT = "You must wait a moment before carving again."
TEXT_CARVE_FULL = ("Players can only carry 5 trophies at a time. Return to the game warden and turn in your existing "
                   "trophies.")
TEXT_NO_CORPSE = "There is nothing nearby to carve."      # [INFERENCE: the Carve button with no corpse in reach]
TEXT_TROPHY = "{} point trophy added to carried trophies."
TEXT_TURNIN = "Ammunition resupplied and {} trophies turned in for {} points ({} new score)"
TEXT_UNLOCKED = "{} unlocked!"
TEXT_WIND = "Wind is now blowing to the {}."
CORPSE_NAME = "an animal corpse"

_TEXT_ENTRY = re.compile(r"(\{ text -?\d+ -?\d+ \d+ )(\d+)")


def intern(layout: str, texts: list[str]) -> tuple[str, list[str]]:
    """The gump as the server sends it: every text entry's index into `texts` rewritten to a line list with each
    string once, in order of first use (live: equal held trophies share a line, hunting_grounds.GroundsLog._hotbar)."""
    lines: list[str] = []
    at: dict[str, int] = {}

    def sub(m):
        s = texts[int(m.group(2))]
        if s not in at:
            at[s] = len(lines)
            lines.append(s)
        return m.group(1) + str(at[s])
    return _TEXT_ENTRY.sub(sub, layout.rstrip("\x00")), lines


def left_text(left_s: float) -> str:
    """The hotbar's and the warden's time left: '15m Left', '4m 15s Left', '20s Left'."""
    left = max(0, int(round(left_s)))
    m, s = divmod(left, 60)
    return f"{m}m Left" if s == 0 else f"{s}s Left" if m == 0 else f"{m}m {s}s Left"


def cheb(a, b) -> int:
    return max(abs(a.x - b.x), abs(a.y - b.y))


@dataclass
class Mobile:
    serial: int
    name: str
    body: int
    x: int
    y: int
    z: int
    d: int = 0
    noto: int = 1
    label: str = ""               # its single-click label


@dataclass
class Deer(Mobile):
    inst: int = 0
    points: int = 2
    home: tuple = (0, 0)
    alive: bool = True
    mood: str = "calm"
    mood_t: float = 0.0           # since when in this mood
    sensed_t: float = -1e9        # the player last seen or sensed (the recovery clock)
    flee_until: float = 0.0       # a grazed deer flees at least till then
    heading: int = 0
    burst: int = 0                # steps left in the burst under way
    next_step: float = 0.0
    next_mood: float = 0.0
    next_label: float = 0.0
    shot: bool = False            # a shot at it is in the air: its mood holds till it lands


class HGWorld:
    def __init__(self, scenario: dict, seed: int = 0, permits: int = 5, walkers: pathfind.Walkers | None = None,
                 followers: bool = False):
        self.sc = sc = scenario
        self.rng = random.Random(seed)                 # the player's draws: shots, the wind, the instance, ejects
        self.drng = random.Random(seed * 7919 + 11)    # the deer's: placement, moods, moves
        self.t = 0.0
        self.out: list[bytes] = []
        self.trace = None
        self.facet = sc["town"]["facet"]
        self.walk = (walkers or pathfind.Walkers()).get(self.facet)
        if self.walk is None:
            raise ValueError(f"no map geometry for facet {self.facet} (install files missing?)")
        self.followers = followers
        self.permits = int(permits)
        tm, dz = sc["timing"], sc["deer"]
        self.weapons = sc["weapons"]
        self.hz = dz["hazard_per_s"]
        self.calm_after = dz["calm_after_s"]
        # other hunters in the instance (about 7 live) spook deer we never see them near: a flee hazard on every calm
        # or nervous deer, wherever we are (others_flee_per_s in the scenario, calibrated on the mood-label shares)
        self.others_flee = dz.get("others_flee_per_s", 0.0)
        self.wind_pen = {30: dz["wind_penalty"]["direct"], 15: dz["wind_penalty"]["adjacent"]}
        mean_burst = sum(BURST) / 2
        self.move = {}                                 # mood -> (step s, mean pause between bursts)
        for mood in ("calm", "nervous", "fleeing"):
            mv = dz["move"][mood]
            step = mv.get("step_s", DEER_WALK_S)
            rate = mv["tiles_per_5s"] / 5.0
            self.move[mood] = (step, max(0.05, mean_burst / rate - mean_burst * step))
        x, y, z = sc["town"]["start"]
        self.player = Mobile(PLAYER_SERIAL, PLAYER_NAME, HUMAN_BODY, x, y, self._z(x, y, z))
        self.mobs: dict[int, Mobile] = {}
        self.npcs: list[Mobile] = []
        self.items: dict[int, dict] = {}               # ground items: corpses and the exit moongates
        self.viewing: set[int] = set()
        self.viewing_items: set[int] = set()
        self.next_serial = 0x00300000
        self.next_item = 0x60000000
        self.gump_n = GUMP_SERIAL0
        self.cid = 0x00400000
        self.pending = None                            # the Shoot cursor up: {"cid"}
        self.s = None                                  # the session under way (_enter), else None
        self.next_inst = self.rng.randrange(len(sc["instances"]))
        self.hidden = False
        self.last_step_t = -1e9
        self.last_hide_t = self.last_carve_t = self.last_track_t = self.last_shot_t = -1e9
        self.hindered_until = 0.0
        self.unhinder_t = None                         # 'You are no longer hindered.' due then
        self.flight = None                             # a shot in the air: (landing time, deer serial, mood at the shot)
        self.respawns: list[tuple[float, int]] = []    # (time, instance)
        self.intents: list[tuple[float, str | None, str]] = []
        self.metrics = {"runs": 0, "sessions": [], "score": [], "trophies": 0, "trophy_points": [],
                        "points_lost_at_expiry": 0, "points_lost_at_gate": 0, "shots": 0, "shots_by_mood": {},
                        "outcomes_by_mood": {}, "kills": 0, "grazes": 0, "misses": 0, "refusals": {},
                        "permits_left": self.permits, "steps": 0, "run_steps": 0, "denied_steps": 0, "fast_steps": 0,
                        "hides": 0, "reveals": 0, "carves": 0, "tracks": 0, "turn_ins": 0, "violations": []}
        self._build()

    # ------------------------------------------------------------ setup
    def _serial(self) -> int:
        self.next_serial += 1
        return self.next_serial

    def _item_serial(self) -> int:
        self.next_item += 1
        return self.next_item

    def _z(self, x, y, near_z=0) -> int:
        return los.surface_z(self.walk, x, y, near_z)

    def _build(self):
        sc = self.sc
        o = sc["town"]["official"]
        ox, oy, oz = o["at"]
        self.official = Mobile(OFFICIAL_SERIAL, o["name"], HUMAN_BODY, ox, oy, self._z(ox, oy, oz), noto=7,
                               label=f"{o['name']} {o['title']}")
        self.npcs.append(self.official)
        self.instances = []
        for i, spec in enumerate(sc["instances"]):
            wx, wy = spec["warden"]
            z = self._z(wx, wy, 0)
            warden = Mobile(self._serial(), "Willow", HUMAN_BODY, wx, wy, z, noto=7, label="Willow the game warden")
            gx, gy = spec["gate"]
            gate = self._item_serial()
            self.items[gate] = {"kind": "gate", "x": gx, "y": gy, "z": self._z(gx, gy, z), "inst": i}
            self.npcs.append(warden)
            self.instances.append({"name": spec["name"], "warden": warden, "entry": tuple(spec["entry"]),
                                   "gate": (gx, gy), "region": tuple(spec["region"]), "z": z, "deer": [],
                                   "bands": None})
        for m in self.npcs:
            self.mobs[m.serial] = m

    def _bands(self, i: int) -> list[tuple[int, int, float]]:
        """The instance's density bands on the map: (y0, y1 inclusive, expected deer), the last band to the south edge."""
        inst = self.instances[i]
        x0, y0, x1, y1 = inst["region"]
        wy = inst["warden"].y
        dens = self.sc["deer"]["density_by_band"]
        out = []
        for k, b in enumerate(dens):
            ya = max(y0, wy + b["dy"][0])
            yb = y1 if k == len(dens) - 1 else min(y1, wy + b["dy"][1] - 1)
            if ya <= yb:
                out.append((ya, yb, b["per_1000"] * (x1 - x0 + 1) * (yb - ya + 1) / 1000.0))
        return out

    def populate(self, i: int):
        """Place instance i's deer (once): each density band's expected count on walkable tiles inside it."""
        inst = self.instances[i]
        if inst["bands"] is not None:
            return
        inst["bands"] = self._bands(i)
        for ya, yb, n in inst["bands"]:
            k = int(n) + (1 if self.drng.random() < n - int(n) else 0)
            for _ in range(k):
                spot = self._spot(i, ya, yb)
                if spot is not None:
                    self._add_deer(i, spot)

    def _spot(self, i: int, ya: int, yb: int, away: bool = False):
        """A walkable tile in the instance's region between rows ya and yb (`away`: out of the player's view)."""
        x0, _, x1, _ = self.instances[i]["region"]
        z0 = self.instances[i]["z"]
        pl = self.player
        for _ in range(60):
            x, y = self.drng.randint(x0, x1), self.drng.randint(ya, yb)
            if away and max(abs(x - pl.x), abs(y - pl.y)) <= VIEW + 2:
                continue
            z = self._z(x, y, z0)
            if any(self.walk.can_walk(x, y, z, d) is not None for d in range(8)):
                return x, y, z
        return None

    def _points(self, dy: int) -> int:
        bands = self.sc["deer"]["points_by_band"]
        b = next((b for b in bands if b["dy"][0] <= dy < b["dy"][1]), bands[0] if dy < bands[0]["dy"][0] else bands[-1])
        vals = list(b["points"].items())
        return int(self.drng.choices([v for v, _ in vals], weights=[n for _, n in vals])[0])

    def _add_deer(self, i: int, at, points: int | None = None) -> Deer:
        x, y, z = at
        inst = self.instances[i]
        pts = self._points(y - inst["warden"].y) if points is None else points
        t, r = self.t, self.drng
        d = Deer(self._serial(), f"{pts} point buck", DEER_BODY, x, y, z, d=r.randrange(8), noto=3, inst=i, points=pts,
                 home=(x, y), heading=r.randrange(8), next_step=t + r.uniform(0, 3), next_mood=t + r.uniform(0, MOOD_S),
                 next_label=t + r.uniform(0, self.sc["timing"]["label_every_s"][1]))
        d.label = d.name
        inst["deer"].append(d)
        self.mobs[d.serial] = d
        return d

    def place_deer(self, x: int, y: int, points: int = 8, mood: str = "calm") -> Deer:
        """A deer of `points` on (x, y) in the instance there, in `mood` (tests, staged scenes)."""
        i = self._inst_of(x, y)
        if i is None:
            raise ValueError(f"({x}, {y}) is in no instance")
        self.populate(i)
        d = self._add_deer(i, (x, y, self._z(x, y, self.instances[i]["z"])), points)
        d.mood, d.mood_t = mood, self.t
        self._deer_seen(d)
        return d

    def remove_deer(self, d: Deer):
        """Take a deer out of the world (no corpse, no respawn)."""
        d.alive = False
        self.instances[d.inst]["deer"].remove(d)
        self.mobs.pop(d.serial, None)
        if d.serial in self.viewing:
            self.viewing.discard(d.serial)
            self.send(P.delete(d.serial))

    def _inst_of(self, x, y) -> int | None:
        for i, inst in enumerate(self.instances):
            x0, y0, x1, y1 = inst["region"]
            if x0 <= x <= x1 and y0 <= y <= y1:
                return i
        return None

    # ------------------------------------------------------------ the link's face
    def login_packets(self) -> list[bytes]:
        pl = self.player
        self.send(P.login(pl.serial, pl.body, pl.x, pl.y, pl.z), self._self_pkt(),
                  P.equip(BACKPACK, 0x0E75, 0x15, pl.serial))
        if self.permits:
            self.send(self._permit_pkt())
        self.send(P.name(pl.serial, pl.name), P.hits(pl.serial, 100, 100), P.mana(pl.serial, 100, 100),
                  P.stam(pl.serial, 100, 100))
        self._view()
        return []

    def send(self, *pkts):
        self.out.extend(pkts)

    def drain(self) -> list[bytes]:
        out, self.out = self.out, []
        return out

    def _ev(self, ev: str, /, **f):
        if self.trace is not None:
            self.trace.event(self.t, ev, f)

    def intent(self, kind: str | None, text: str):
        self.intents.append((self.t, kind, text))
        del self.intents[:-50]

    @property
    def session(self) -> dict | None:
        return self.s

    # ------------------------------------------------------------ helpers
    def _self_pkt(self) -> bytes:
        pl = self.player
        return P.mobile(pl.serial, pl.body, pl.x, pl.y, pl.z, noto=1, flags=0x80 if self.hidden else 0, d=pl.d)

    def _permit_pkt(self) -> bytes:
        return P.contained(PERMIT, self.sc["permit"]["graphic"], self.permits, BACKPACK)

    def _sys(self, text: str):
        self._ev("sys", text=text)
        self.send(P.system_text(text))

    def _over(self, text: str):
        """A line over our own head (the session's clock lines, the kill, a trophy, an unlock)."""
        self._ev("say", m=self.player.serial, text=text)
        self.send(P.says(self.player.serial, self.player.name, text, kind=0, body=self.player.body))

    def _say(self, d: Deer, text: str):
        if d.serial in self.viewing:
            self.send(P.says(d.serial, d.name, text, kind=0, hue=LABEL_HUE, body=d.body))

    def _refuse(self, text: str, cliloc: int | None = None):
        r = self.metrics["refusals"]
        r[text] = r.get(text, 0) + 1
        if cliloc is None:
            self._sys(text)
        else:
            self.send(P.cliloc(cliloc))

    def _violation(self, what: str, **f):
        self.metrics["violations"].append({"t": round(self.t, 1), "what": what, **f})

    def _tier(self, turned_in: int) -> int:
        return max(i for i, w in enumerate(self.weapons) if turned_in >= w[0])

    def _here(self) -> int | None:
        return self._inst_of(self.player.x, self.player.y)

    # ------------------------------------------------------------ view
    def _show(self, m: Mobile):
        self.send(P.mobile(m.serial, m.body, m.x, m.y, m.z, noto=m.noto, d=m.d), P.name(m.serial, m.name),
                  P.hits(m.serial, 100, 100))

    def _show_item(self, s: int, it: dict):
        if it["kind"] == "gate":
            self.send(P.ground_item(s, GATE_GRAPHIC, 1, it["x"], it["y"], it["z"], hue=GATE_HUE))
        else:
            self.send(P.ground_item(s, combat.CORPSE_GRAPHIC, DEER_BODY, it["x"], it["y"], it["z"]),
                      P.corpse_flags(s, it["owner"], CORPSE_NAME, 3))

    def _view(self):
        pl = self.player
        i = self._here()
        cands = self.npcs + (self.instances[i]["deer"] if i is not None else [])
        seen = set()
        for m in cands:
            if cheb(m, pl) <= VIEW:
                seen.add(m.serial)
                if m.serial not in self.viewing:
                    self.viewing.add(m.serial)
                    self._show(m)
        for s in self.viewing - seen:
            self.viewing.discard(s)
            self.send(P.delete(s))
        for s, it in self.items.items():
            near = max(abs(it["x"] - pl.x), abs(it["y"] - pl.y)) <= VIEW
            if near and s not in self.viewing_items:
                self.viewing_items.add(s)
                self._show_item(s, it)
            elif not near:
                self.viewing_items.discard(s)

    def _deer_seen(self, d: Deer, run: bool = False):
        """After a deer moved: its 0x77 in view, shown entering, deleted leaving (the server's 0x1D, as World._view)."""
        if cheb(d, self.player) <= VIEW and self._here() == d.inst:
            if d.serial in self.viewing:
                self.send(P.move(d.serial, d.x, d.y, d.z, d.d, run=run))
            else:
                self.viewing.add(d.serial)
                self._show(d)
        elif d.serial in self.viewing:
            self.viewing.discard(d.serial)
            self.send(P.delete(d.serial))

    def _teleport(self, to):
        pl = self.player
        pl.x, pl.y = to[0], to[1]
        pl.z = self._z(to[0], to[1], to[2] if len(to) > 2 else pl.z)
        self.send(self._self_pkt())
        self._view()

    def _reveal(self):
        if self.hidden:
            self.hidden = False
            self.metrics["reveals"] += 1
            self.send(self._self_pkt())

    # ------------------------------------------------------------ clock
    def run_until(self, t: float):
        while self.t + TICK_S <= t + 1e-9:
            self.t += TICK_S
            self._tick()

    def _tick(self):
        t = self.t
        if self.flight is not None and t >= self.flight[0]:
            self._land()
        if self.unhinder_t is not None and t >= self.unhinder_t:
            self.unhinder_t = None
            self._sys(HELD_END_TEXT)
        s = self.s
        if s is not None:
            e = t - s["t0"]
            for minutes, at in ((10, 300.0), (5, 600.0)):
                if e >= at and minutes not in s["warned"]:
                    s["warned"].add(minutes)
                    self._over(MINUTES_TEXT.format(minutes))
            if e >= self.sc["session_s"]:
                self._end("expired")
        s = self.s
        if s is not None:
            if t >= s["next_wind"]:
                s["next_wind"] += self.sc["wind_every_s"]
                s["wind"] = (s["wind"] + self.rng.choice((-1, 1))) % 8
                self._sys(TEXT_WIND.format(hg.OCTANTS[s["wind"]]))
            if t >= s["next_hotbar"]:
                s["next_hotbar"] += self.sc["hotbar_every_s"]
                self._send_hotbar()
            dy = self.player.y - self.instances[s["inst"]]["warden"].y
            s["deepest_dy"] = max(s["deepest_dy"], dy)
        for c in [c for c, it in self.items.items() if it["kind"] == "corpse" and t >= it["decay_t"]]:
            self._remove_corpse(c)
        if self.respawns and any(r[0] <= t for r in self.respawns):
            due = [r for r in self.respawns if r[0] <= t]
            self.respawns = [r for r in self.respawns if r[0] > t]
            for _, i in due:
                self._respawn(i)
        i = self._here()
        if i is not None:
            self._deer_tick(i)

    # ------------------------------------------------------------ client packets
    def c2s(self, p: bytes, seq: int | None = None):
        pid = p[0]
        if pid == 0x02:
            self._walk(p)
        elif pid == 0x06:
            self._dclick(int.from_bytes(p[1:5], "big"))
        elif pid == 0x09:
            self._click(int.from_bytes(p[1:5], "big"))
        elif pid == 0x12 and len(p) > 4:
            if p[3] == 0x24:
                try:
                    sid = int(p[4:-1].decode("ascii").split()[0])
                except (ValueError, IndexError):
                    return
                self._skill(sid)
            elif p[3] in (0x27, 0x56):
                self._violation("spell")
        elif pid == 0x6C:
            self._target(p)
        elif pid == 0xB1:
            self._gump_reply(p)
        elif pid == 0x72:
            self._violation("war mode")
            self.send(P.warmode(bool(p[1])))
        elif pid == 0x05:
            self._violation("attack", serial=hex(int.from_bytes(p[1:5], "big")))
        elif pid in (0x03, 0xAD):
            self._violation("speech")
        elif pid == 0xBF and p[3:5] == b"\x00\x1c":
            self._violation("spell")
        elif pid == 0xBF and p[3:5] == b"\x00\x15":
            self._violation("context menu pick")
        elif pid == 0xFF and p[3:7] == b"\x00\x00\x00\x04":
            self._violation("spell")

    def _walk(self, p: bytes):
        pl, t = self.player, self.t
        seq, d, run = p[2], p[1] & 7, bool(p[1] & 0x80)
        if self.flight is not None or t < self.hindered_until:
            self.metrics["denied_steps"] += 1
            self.send(P.deny_walk(seq, pl.x, pl.y, pl.z, pl.d))
            return
        if d != pl.d:
            pl.d = d
            self.send(P.confirm_walk(seq))
            return
        to = self.walk.can_walk(pl.x, pl.y, pl.z, d)
        if to is None:
            self.metrics["denied_steps"] += 1
            self.send(P.deny_walk(seq, pl.x, pl.y, pl.z, pl.d))
            return
        pace = self.sc["player"]["run_s" if run else "walk_s"]
        if t - self.last_step_t < pace * FAST_SLACK:
            self.metrics["fast_steps"] += 1
        pl.x, pl.y, pl.z = to
        self.send(P.confirm_walk(seq))
        self.metrics["steps"] += 1
        self.metrics["run_steps"] += run
        self.last_step_t = t
        if self.hidden:
            i = self._here()
            if run or (i is not None and any(o.x == pl.x and o.y == pl.y for o in self.instances[i]["deer"])):
                self._reveal()
        s = self.s
        if s is not None and (pl.x, pl.y) == self.instances[s["inst"]]["gate"]:
            # its menu, as live (2026-11-23 20:06:53); Confirm ends the session (_gump_reply)
            menu = self.sc["gump_templates"]["exit_gate"]
            self._send_gump(self.sc["gumps"]["exit_gate"], menu["layout"], menu["lines"])
        self._view()

    def _click(self, s: int):
        m = self.mobs.get(s)
        if m is not None:
            self.send(P.says(s, m.name, m.label or m.name, kind=6, body=m.body))
        elif s == PERMIT and self.permits:
            self.send(P.says(s, self.sc["permit"]["name"], f"{self.sc['permit']['name']} : {self.permits}", kind=6))
        elif s in self.items and self.items[s]["kind"] == "corpse":
            self.send(P.says(s, CORPSE_NAME, CORPSE_NAME, kind=6))

    def _dclick(self, s: int):
        if s == BACKPACK:
            self.send(P.open_container(BACKPACK, 0x3C))
            if self.permits:
                self.send(self._permit_pkt())
            return
        m = self.mobs.get(s)
        if m is self.official:
            if cheb(m, self.player) > NPC_R:
                self._refuse("too far", FAR_CLILOC)
                return
            t = self.sc["gump_templates"]["official"]
            self._send_gump(self.sc["gumps"]["official"], *intern(t["layout"], t["lines"]))
        elif m is not None and any(m is inst["warden"] for inst in self.instances):
            if cheb(m, self.player) > NPC_R:
                self._refuse("too far", FAR_CLILOC)
                return
            self._send_warden()
        elif isinstance(m, Deer):
            self._shoot(s)
        elif s in self.items and self.items[s]["kind"] == "corpse":
            self._carve(s)

    def _skill(self, sid: int):
        if sid == 21:
            self._hide()
        elif sid == 38:
            self._track()
        elif sid == 19:
            self._carve(None)

    def _target(self, p: bytes):
        from world.parsers import parse_packet
        f = parse_packet("c2s", p)
        pend, self.pending = self.pending, None
        if pend is None or f.get("cursor_id") != pend["cid"]:
            return
        serial = f.get("serial") or 0
        if serial:
            self._shoot(serial)

    def _gump_reply(self, p: bytes):
        from world.parsers import parse_packet
        f = parse_packet("c2s", p)
        gid, button = f.get("gump_id"), f.get("button_id", 0)
        ids = self.sc["gumps"]
        if gid == ids["official"] and button == self.sc["gump_templates"]["official"]["enter_button"]:
            if cheb(self.official, self.player) > NPC_R:
                self._refuse("too far", FAR_CLILOC)
                return
            self._enter()
        elif gid == ids["warden"] and button == self.sc["gump_templates"]["warden"]["turn_in_button"]:
            if self.s is not None and cheb(self.instances[self.s["inst"]]["warden"], self.player) <= NPC_R:
                self._turn_in()
        elif gid == ids["hotbar"] and self.s is not None:
            if button == 1:
                self._carve(None)
            elif button == 2:
                self._hide()
            elif button == 3:
                self._track()
            elif button == 4:
                self.cid += 1
                self.pending = {"cid": self.cid}
                self._sys(TEXT_SHOOT_PROMPT)
                self.send(P.cursor(self.cid, 1, 0))
        elif gid == ids.get("exit_gate") and button == self.sc["gump_templates"]["exit_gate"]["confirm_button"]:
            s, pl = self.s, self.player
            if s is not None:
                gx, gy = self.instances[s["inst"]]["gate"]
                if max(abs(pl.x - gx), abs(pl.y - gy)) > 1:
                    return
                if not s.get("gate_asked"):
                    # the first Confirm only asks again (live 2026-11-23 22:41:52.6); the second ends the session
                    s["gate_asked"] = True
                    self._sys(EXIT_GATE_AGAIN)
                    menu = self.sc["gump_templates"]["exit_gate"]
                    self._send_gump(gid, menu["layout"], menu["lines"])
                    return
                self._end("gate")

    # ------------------------------------------------------------ gumps
    def _send_gump(self, gump_id: int, layout: str, lines: list[str]):
        self.gump_n += 1
        self.send(P.gump(self.gump_n, gump_id, layout, lines))

    def _send_warden(self):
        s = self.s
        t = self.sc["gump_templates"]["warden"]
        texts = list(t["lines"])
        if s is not None:
            texts[19] = f"{sum(s['held'])} Pts"
            texts[21] = str(s["score"])
            texts[22] = left_text(self.sc["session_s"] - (self.t - s["t0"]))
        self._send_gump(self.sc["gumps"]["warden"], *intern(t["layout"], texts))

    def hotbar(self) -> tuple[str, list[str]]:
        """The hotbar's layout and lines now, in the live format (gump_templates.hotbar_full / hotbar_empty)."""
        s = self.s
        texts: list[str] = []

        def text(x, y, hue, s_):
            texts.append(s_)
            return f"{{ text {x} {y} {hue} {len(texts) - 1} 18 0 1 0 0 0 }}"
        art = self.weapons[self._tier(s["turned_in"])][4]
        parts = ["{ resizepic 13 13 11571 149 548 }", text(52, 26, 2606, "Score"), text(101, 26, 2655, str(s["score"])),
                 "{ tilepic 20 42 6163 }", text(55, 46, 2655, left_text(self.sc["session_s"] - (self.t - s["t0"]))),
                 "{ gumppictiled 25 79 126 3 96 }", text(50, 70, 149, "Held Trophies")]
        for k, pts in enumerate(s["held"]):
            a = TROPHY_ART0 + pts // 2
            parts += [f"{{ tilepic {TROPHY_X.get(a, 40)} {TROPHY_Y[k]} {a} }}",
                      text(101, TROPHY_TEXT_Y[k], 2655, f"{pts} Pt")]
        hide = BTN_ON if self.hidden else BTN_UP
        shoot = BTN_ON if self.t - self.last_shot_t < self.sc["timing"]["shot_cooldown_s"] else BTN_UP
        wx, wy = WEAPON_TILE.get(art, (44, 404))
        parts += ["{ gumppictiled 25 274 126 3 96 }", "{ tilepic 52 287 31188 }", "{ button 95 285 2151 2154 1 0 1 }",
                  "{ tilepic 55 318 51173 }", f"{{ button 95 325 {hide[0]} {hide[1]} 1 0 2 }}",
                  "{ tilepichue 43 363 63989 2499 }", "{ button 95 370 2151 2154 1 0 3 }",
                  f"{{ tilepic {wx} {wy} {art} }}", f"{{ button 95 414 {shoot[0]} {shoot[1]} 1 0 4 }}",
                  "{ gumppictiled 25 461 126 3 96 }", text(45, 465, 149, "Ammo"),
                  text(96, 465, 2655, f"{s['ammo']}/{self.sc['ammo']}"),
                  "{ gumppictiled 25 487 126 3 96 hue=2428 }", "{ tilepic 15 488 71754 }", text(46, 508, 2603, "Wind"),
                  f"{{ tilepic 94 492 {WIND_ART0 + s['wind']} }}"]
        return intern("".join(parts), texts)

    def _send_hotbar(self):
        """Close and re-send, as live every 5 s (the Oxygen gump's pattern)."""
        gid = self.sc["gumps"]["hotbar"]
        self.send(P.close_gump(gid))
        self._send_gump(gid, *self.hotbar())

    # ------------------------------------------------------------ the session
    def _enter(self):
        if self.s is not None:
            return
        if self.followers:
            self._refuse(TEXT_FOLLOWERS)
            return
        if self.permits <= 0:
            self._refuse(TEXT_NO_PERMIT)
            return
        self.permits -= 1
        self.metrics["permits_left"] = self.permits
        self.send(self._permit_pkt() if self.permits else P.delete(PERMIT))
        i = self.next_inst
        self.next_inst = (i + 1) % len(self.instances)
        self.populate(i)
        inst = self.instances[i]
        t = self.t
        self.s = {"inst": i, "t0": t, "held": [], "turned_in": 0, "score": 0, "ammo": self.sc["ammo"],
                  "wind": self.rng.randrange(8), "next_wind": t + self.sc["wind_every_s"], "next_hotbar": t,
                  "warned": set(), "shots": 0, "kills": 0, "grazes": 0, "misses": 0, "trophies": [], "kill_dy": [],
                  "turn_ins": 0, "deepest_dy": 0}
        self.metrics["runs"] += 1
        self.hidden = False
        self.pending = None
        self.last_shot_t = self.last_track_t = -1e9
        self._teleport((*inst["entry"], inst["z"]))
        self._over(hg.START_TEXT)
        self._send_hotbar()
        self.s["next_hotbar"] = t + self.sc["hotbar_every_s"]

    def _end(self, why: str):
        """The session's end: the expiry (its line) or the exit moongate; held trophies are lost; out to Prevalia."""
        s, m = self.s, self.metrics
        lost = sum(s["held"])
        if why == "expired":
            self._over(hg.EXPIRED_TEXT)
            m["points_lost_at_expiry"] += lost
        else:
            m["points_lost_at_gate"] += lost
        m["score"].append(s["score"])
        m["sessions"].append({"instance": self.instances[s["inst"]]["name"], "t0": round(s["t0"], 1),
                              "t_end": round(self.t, 1), "end": why, "score": s["score"], "turned_in": s["turned_in"],
                              "trophies": list(s["trophies"]), "held_lost": list(s["held"]), "turn_ins": s["turn_ins"],
                              "shots": s["shots"], "kills": s["kills"], "grazes": s["grazes"], "misses": s["misses"],
                              "kill_dy": list(s["kill_dy"]), "deepest_dy": s["deepest_dy"]})
        if self.flight is not None:
            d = self.mobs.get(self.flight[1])
            if isinstance(d, Deer):
                d.shot = False
        self.s = None
        self.flight = self.pending = self.unhinder_t = None
        self.hindered_until = 0.0
        self.send(P.close_gump(self.sc["gumps"]["hotbar"]))
        x, y = self.rng.choice(self.sc["town"]["eject_to"])
        self.hidden = why == "expired"     # the forum post: ejected players arrive hidden
        self._teleport((x, y, self.sc["town"]["start"][2]))

    def _turn_in(self):
        s, m = self.s, self.metrics
        held = s["held"]
        before = self._tier(s["turned_in"])
        s["score"] += sum(held)
        s["turned_in"] += len(held)
        s["trophies"] += held
        s["turn_ins"] += 1
        s["ammo"] = self.sc["ammo"]
        m["trophies"] += len(held)
        m["trophy_points"] += held
        m["turn_ins"] += 1
        self._sys(TEXT_TURNIN.format(len(held), sum(held), s["score"]))
        s["held"] = []
        for k in range(before + 1, self._tier(s["turned_in"]) + 1):
            self._over(TEXT_UNLOCKED.format(self.weapons[k][1]))

    # ------------------------------------------------------------ actions
    def _hide(self):
        t, tm = self.t, self.sc["timing"]
        if t - self.last_hide_t < tm["hide_after_hide_s"] or t - self.last_carve_t < tm["hide_after_carve_s"]:
            self._refuse(TEXT_HIDE_WAIT)
            return
        self.last_hide_t = t
        self.hidden = True
        self.metrics["hides"] += 1
        self._sys(TEXT_HIDE)
        self.send(self._self_pkt())

    def _track(self):
        if self.s is None:
            return
        if self.t - self.last_track_t < self.sc["timing"]["hide_after_hide_s"]:
            self._refuse(TEXT_TRACK_WAIT)
            return
        self.last_track_t = self.t
        self.metrics["tracks"] += 1
        self._sys(TEXT_TRACK)

    def _shoot(self, serial: int):
        """A shot at `serial` (its refusals in an order [INFERENCE]: the live lines don't tell it)."""
        s, t, pl, tm = self.s, self.t, self.player, self.sc["timing"]
        d = self.mobs.get(serial)
        if s is None or not isinstance(d, Deer) or not d.alive or serial not in self.viewing:
            self._refuse(TEXT_NOT_DEER)
            return
        if len(s["held"]) >= self.sc["hold_max"]:
            self._refuse(TEXT_HOLD_FULL)
            return
        if t - self.last_shot_t < tm["shot_cooldown_s"]:
            self._refuse(TEXT_SHOT_WAIT)
            return
        if t - self.last_step_t < tm["stationary_s"]:
            self._refuse(TEXT_STATIONARY)
            return
        if cheb(d, pl) > self.weapons[self._tier(s["turned_in"])][2]:
            self._refuse(TEXT_RANGE)
            return
        if s["ammo"] <= 0:
            self._refuse(TEXT_NO_AMMO)
            return
        s["ammo"] -= 1
        s["shots"] += 1
        self.last_shot_t = t
        m = self.metrics
        m["shots"] += 1
        m["shots_by_mood"][d.mood] = m["shots_by_mood"].get(d.mood, 0) + 1
        self._reveal()
        # the target's mood holds while the shot is in the air: live calm shots hit 26 of 31 (84 %, the post's 90 %),
        # no room for the 0.3/s flee hazard a revealed shooter puts on it over the 1.3-1.75 s flight [INFERENCE]
        d.shot = True
        self.flight = (t + self.rng.uniform(*tm["shot_resolve_s"]), serial, d.mood)
        dz = self.sc["deer"]
        for o in self.instances[d.inst]["deer"]:
            if o is not d and o.mood != "fleeing" and cheb(o, d) <= dz["shot_scare_r"] \
                    and self.drng.random() < dz["shot_scare_p"]:
                self._set_mood(o, "fleeing")

    def _land(self):
        _, serial, mood0 = self.flight
        self.flight = None
        t, s, m = self.t, self.s, self.metrics
        self.hindered_until = self.unhinder_t = t + UNHINDER_AFTER_SHOT_S
        d = self.mobs.get(serial)
        out = m["outcomes_by_mood"].setdefault(mood0, {"kill": 0, "graze": 0, "miss": 0})
        if not isinstance(d, Deer) or not d.alive:
            out["miss"] += 1
            m["misses"] += 1
            s["misses"] += 1
            return
        d.shot = False
        tier = self.weapons[self._tier(s["turned_in"])]
        # past the weapon's range when the shot lands (it moved during the ~1.5 s flight): never a kill. Live, 8 such
        # shots (2026-11-22/23, the user's and the runner's): 0 kills, 6 grazed, 2 missed, against 77 % kills for calm
        # bucks that stayed in range; so the hit roll stands and a hit becomes a graze
        beyond = cheb(d, self.player) > tier[2]
        if self.rng.random() < self.sc["hit"][d.mood]:
            if beyond or self.rng.random() < tier[3]:
                out["graze"] += 1
                m["grazes"] += 1
                s["grazes"] += 1
                self._say(d, "*grazed*")
                self._set_mood(d, "fleeing", say=False)
                d.flee_until = t + self.sc["deer"]["graze_flee_s"]
            else:
                out["kill"] += 1
                self._kill(d)
        else:
            out["miss"] += 1
            m["misses"] += 1
            s["misses"] += 1
            self._set_mood(d, "fleeing")

    def _kill(self, d: Deer):
        t, s, tm = self.t, self.s, self.sc["timing"]
        corpse = self._item_serial()
        self.items[corpse] = {"kind": "corpse", "x": d.x, "y": d.y, "z": d.z, "owner": d.serial, "points": d.points,
                              "carve_t": t + self.rng.uniform(*tm["carve_after_kill_s"]),
                              "decay_t": t + tm["corpse_decay_s"], "inst": d.inst}
        self._over(TEXT_FELLED)
        if d.serial in self.viewing:
            self.send(P.display_death(d.serial, corpse), P.delete(d.serial))
        self.viewing.discard(d.serial)
        self.viewing_items.add(corpse)
        self._show_item(corpse, self.items[corpse])
        d.alive = False
        self.instances[d.inst]["deer"].remove(d)
        del self.mobs[d.serial]
        self.respawns.append((t + self.drng.uniform(*self.sc["deer"]["respawn_s"]), d.inst))
        self.metrics["kills"] += 1
        s["kills"] += 1
        s["kill_dy"].append(d.y - self.instances[d.inst]["warden"].y)

    def _remove_corpse(self, c: int):
        self.items.pop(c, None)
        self.viewing_items.discard(c)
        self.send(P.delete(c))

    def _carve(self, serial: int | None):
        s, t, pl, tm = self.s, self.t, self.player, self.sc["timing"]
        if s is None:
            return
        if serial is None:
            near = sorted((max(abs(it["x"] - pl.x), abs(it["y"] - pl.y)), c) for c, it in self.items.items()
                          if it["kind"] == "corpse")
            near = [c for dist, c in near if dist <= tm["carve_range"]]
            if not near:
                self._refuse(TEXT_NO_CORPSE)
                return
            serial = near[0]
        it = self.items[serial]
        if max(abs(it["x"] - pl.x), abs(it["y"] - pl.y)) > tm["carve_range"]:
            self._refuse("too far", FAR_CLILOC)
            return
        if t < it["carve_t"]:
            self._refuse(TEXT_CARVE_WAIT)
            return
        if len(s["held"]) >= self.sc["hold_max"]:
            self._refuse(TEXT_CARVE_FULL)
            return
        s["held"].append(it["points"])
        self.metrics["carves"] += 1
        self._over(TEXT_TROPHY.format(it["points"]))
        self._remove_corpse(serial)
        self.last_carve_t = t
        self.hindered_until = self.unhinder_t = t + tm["hindered_after_carve_s"]

    # ------------------------------------------------------------ deer
    def _respawn(self, i: int):
        bands = self.instances[i]["bands"]
        ya, yb, _ = self.drng.choices(bands, weights=[b[2] for b in bands])[0]
        spot = self._spot(i, ya, yb, away=True)
        if spot is not None:
            self._deer_seen(self._add_deer(i, spot))

    def _rate(self, key: str, dist: int, hidden: bool) -> float:
        for maxd, r in self.hz["hidden" if hidden else "visible"].get(key) or ():
            if dist <= maxd:
                return r
        return 0.0

    def wind_x(self, d: Deer) -> float:
        """The hazards' factor from the wind: 1 + its penalty when the deer is (somewhat) downwind of us."""
        if self.s is None:
            return 1.0
        pen = hg.downwind((self.player.x, self.player.y), (d.x, d.y), hg.OCTANTS[self.s["wind"]])
        return 1.0 + self.wind_pen.get(pen, 0.0)

    def hazards(self, d: Deer, dist: int | None = None) -> tuple[float, float]:
        """(to fleeing, to nervous) per second for deer `d` now (the scenario's hazard_per_s by our hidden state and
        distance, times the wind's factor)."""
        dist = cheb(d, self.player) if dist is None else dist
        if d.mood == "calm":
            rf, rn = self._rate("calm_flee", dist, self.hidden), self._rate("calm_nervous", dist, self.hidden)
        elif d.mood == "nervous":
            rf, rn = self._rate("nervous_flee", dist, self.hidden), 0.0
        else:
            return 0.0, 0.0
        w = self.wind_x(d) if rf or rn else 1.0
        return rf * w + self.others_flee, rn * w

    def _set_mood(self, d: Deer, mood: str, say: bool = True):
        old = d.mood
        d.mood, d.mood_t = mood, self.t
        if mood == "fleeing":
            d.burst = 0
            d.next_step = min(d.next_step, self.t)
        if say and mood != old:
            if mood == "fleeing":
                self._say(d, "*flees*")
            elif mood == "nervous" and old == "calm":
                self._say(d, "*looks nervous*")

    def _mood(self, d: Deer, dist: int):
        if d.shot:
            return
        t = self.t
        if dist <= (SENSE_HIDDEN_R if self.hidden else SENSE_VISIBLE_R):
            d.sensed_t = t
        rf, rn = self.hazards(d, dist)
        total = rf + rn
        if total > 0 and self.drng.random() < 1.0 - math.exp(-total * MOOD_S):
            self._set_mood(d, "fleeing" if self.drng.random() * total < rf else "nervous")
            return
        quiet = t - max(d.sensed_t, d.mood_t) >= self.calm_after
        if d.mood == "fleeing" and quiet and t >= d.flee_until:
            self._set_mood(d, "nervous", say=False)
        elif d.mood == "nervous" and quiet:
            self._set_mood(d, "calm", say=False)

    def _turn(self, weights) -> int:
        r = self.drng.random()
        for k, w in weights:
            r -= w
            if r < 0:
                break
        return k * self.drng.choice((-1, 1)) if k else 0

    def _deer_step(self, d: Deer):
        t, pl = self.t, self.player
        step, pause = self.move[d.mood]
        fleeing = d.mood == "fleeing"
        if d.burst <= 0:
            d.burst = self.drng.randint(*BURST)
            if not fleeing:
                hx, hy = d.home
                home = hg._octant(hx - d.x, hy - d.y) if max(abs(hx - d.x), abs(hy - d.y)) > HOME_R else None
                d.heading = home if home is not None else (d.heading + self._turn(TURN_WANDER)) % 8
        if fleeing:
            away = hg._octant(d.x - pl.x, d.y - pl.y) if cheb(d, pl) <= FLEE_FROM_R else None
            d.heading = ((d.heading if away is None else away) + self._turn(TURN_FLEE)) % 8
        x0, y0, x1, y1 = self.instances[d.inst]["region"]
        for off in (0, 1, -1, 2, -2, 3, -3):
            k = (d.heading + off) % 8
            to = self.walk.can_walk(d.x, d.y, d.z, k)
            if to is not None and x0 <= to[0] <= x1 and y0 <= to[1] <= y1:
                d.d = k
                d.x, d.y, d.z = to
                self._deer_seen(d, run=fleeing)
                break
        d.burst -= 1
        d.next_step = t + step + (self.drng.expovariate(1.0 / pause) if d.burst <= 0 else 0.0)

    def _deer_tick(self, i: int):
        t, pl = self.t, self.player
        lab = self.sc["timing"]["label_every_s"]
        for d in self.instances[i]["deer"]:
            dist = max(abs(d.x - pl.x), abs(d.y - pl.y))
            if dist > ACTIVE_R:
                continue
            if t >= d.next_mood:
                d.next_mood = t + MOOD_S
                self._mood(d, dist)
            if t >= d.next_step:
                self._deer_step(d)
            if t >= d.next_label:
                d.next_label = t + self.drng.uniform(*lab)
                self._say(d, f"({d.mood})")

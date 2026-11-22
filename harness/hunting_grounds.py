"""Hunting Grounds (deer event 2026-11-21 to 11-28): the run's clock, trophies and weapon tier, and the
strategy advice the viz shows on the map (docs/VISUALIZER.md §2.22; user request 2026-11-22: "show
recommended range to move to, recommended points of deer to shoot, when we must return to the lodge based
on time remaining").

The rules and the measurements behind every constant: docs/research/HUNTING_GROUNDS.md (Logan's runs of
2026-11-22, capture 20261122_140905). The strategy (§4 there) in short:
- trophy slots (5 a trip) are the constraint, and buck value rises with distance south of the lodge
  (BANDS), so hunt deep and shoot only calm bucks;
- weapon upgrades (TIERS: every 3 trophies turned in, reset each run) cut the kill time;
- held trophies are lost at expiry, so head back while the run home still fits in the time left.

GroundsLog (one per session, fed the world model's events like fos_field.FieldLog; silent: no proxy events,
no sound) keeps the run from the server's lines and status() turns it into the state port's `grounds`. The
plan is a dynamic program over the rest of the run (lodge_value): from where we stand, either run home now
or go to a band and take k more trophies, then the best continuation from the lodge. Its kill-time model is
the doc's: T = a / (hit * (1 - graze)) + carve, a = KILL_A_S calibrated to the observed ~30 s per kill.
"""
from __future__ import annotations

import functools
import math
import re

LODGE = (8342, 89)            # the Game Warden (Willow) at the lodge, live 2026-11-22; entry ~(8340, 90)
REGION = (8218, 72, 8474, 330)   # every player step and buck position seen: x0, y0, x1, y1 (inclusive)
LEAVE_R = 400                 # farther than this from the lodge: we left (the exit moongate, a recall)
SESSION_S = 900.0             # the start line -> "expired": 904 s and 899 s in Logan's runs
END_GRACE_S = 10.0            # past the end this long without the expiry line: the session is over
RUN_S = 0.2                   # s per tile running visible (1918 of 2131 visible steps)
STEALTH_S = 0.4               # s per tile hidden (1153 of 1302 hidden steps)
WARDEN_S = 10.0               # at the warden: the double-click and the turn-in button
RETURN_MARGIN_S = 20.0        # on top of the run home: the warden, a detour, the 5 s hotbar clock
END_MARGIN_S = 15.0           # a trip planned from the lodge ends this long before the expiry
VIEW_R = 18                   # every buck came into view exactly 18 tiles off: farther range is wasted
NERVOUS_R = 10                # a hidden player made deer nervous within 10 tiles (17 of 18)
HIT_CALM = 0.9
KILL_A_S = 18.0               # search + approach + the shot, per attempt (fits ~30 s per kill observed)
CARVE_S = 6.0
HOLD_MAX = 5
STATE_TTL_S = 15.0            # a deer's mood label is re-sent about every 5 s; older: unknown
WARN_SLACK_S = 30.0           # holding trophies with less spare time than this: the badge warns
# (dy0, dy1, mean points) by tiles south of the lodge: the buck values seen (450 bucks)
BANDS = ((0, 60, 2.1), (60, 120, 2.7), (120, 160, 4.2), (160, 200, 7.6), (200, 260, 9.1))
# (trophies turned in, weapon, range, graze), from the warden gump
TIERS = ((0, "starter bow", 12, 0.25), (3, "Recurve Bow", 14, 0.20), (6, "Hunting Bow", 16, 0.15),
         (9, "Great Bow", 18, 0.10), (12, "Ancient Rifle", 20, 0.05))

START_TEXT = "*You now have 15 minutes in this realm*"          # over our own head
EXPIRED_TEXT = "Your permit for the Hunting Grounds has expired."
MINUTES_LEFT = re.compile(r"^You now have (\d+) minutes left remaining in this realm\.$")
TROPHY = re.compile(r"^(\d+) point trophy added to carried trophies\.$")
TURNIN = re.compile(r"^Ammunition resupplied and (\d+) trophies turned in for (\d+) points \((\d+) new score\)$")
WIND = re.compile(r"^Wind is now blowing to the (north|northeast|east|southeast|south|southwest|west|northwest)\.$")
BUCK = re.compile(r"^(\d+) point buck$")
GUMP_LEFT = re.compile(r"^(?:(\d+)m )?(\d+)s Left$")            # the warden gump: "10m 30s Left", "10s Left"
AMMO = re.compile(r"^(\d+)/(\d+)$")
MOODS = {"(calm)": "calm", "(nervous)": "nervous", "(fleeing)": "fleeing", "*flees*": "fleeing",
         "*looks nervous*": "nervous", "*grazed*": "fleeing"}
OCTANTS = ("north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest")


def cheb(ax, ay, bx, by) -> int:
    return max(abs(ax - bx), abs(ay - by))


def tier(turned_in: int) -> int:
    return max(i for i, t in enumerate(TIERS) if turned_in >= t[0])


def kill_s(turned_in: int) -> float:
    """Expected seconds per trophy with the weapon `turned_in` trophies unlock: attempts until one kills a
    calm buck (a graze loses the deer like a miss), less stealth walking the longer the range (up to the view),
    plus the carve."""
    _, _, rng, graze = TIERS[tier(turned_in)]
    a = KILL_A_S - STEALTH_S * (min(rng, VIEW_R) - TIERS[0][2])
    return a / (HIT_CALM * (1 - graze)) + CARVE_S


def band_y(band, lodge_y: int = LODGE[1]) -> tuple[int, int]:
    """A band's y range on the map, clipped to the region."""
    return lodge_y + band[0], min(lodge_y + band[1], REGION[3])


@functools.lru_cache(maxsize=None)
def lodge_value(t5: int, turned_in: int) -> float:
    """The best expected points of the rest of a run from the warden with t5 * 5 s left and `turned_in`
    trophies already turned in this run (whole trips: out, k kills, back, the warden)."""
    budget = t5 * 5 - END_MARGIN_S
    tk = kill_s(turned_in)
    best = 0.0
    for lo, hi, v in BANDS:
        dyc = (lo + hi) / 2
        for k in range(1, HOLD_MAX + 1):
            trip = 2 * RUN_S * dyc + WARDEN_S + k * tk
            if trip > budget:
                break
            best = max(best, k * v + lodge_value(int((t5 * 5 - trip) // 5), turned_in + k))
    return best


def _rest(left_s: float, turned_in: int) -> float:
    return lodge_value(int(max(0.0, left_s) // 5), turned_in) if left_s > 0 else 0.0


def plan(x: int, y: int, left_s: float, held: list[int], turned_in: int, lodge=LODGE) -> dict:
    """What to do now (docs/research/HUNTING_GROUNDS.md §4): fill this trip's free trophy slots with the most
    points that still fit before the run home, else run home.

    For each band: the kills that fit (kills_max: travel there, kills at the weapon's kill time, the run home
    from the band or from here if farther, RETURN_MARGIN_S) and this trip's points, min(free slots, kills_max)
    times the band's mean. The best band wins (deeper on a tie); none fits, or the slots are full: return.
    The zone is the chosen band and its neighbours worth at least 80% of its trip points (the deep south is
    one zone, not two bands to flip between as we walk).

    Why not the dynamic program's best value: its values are flat near the top (options within ~5 points;
    whole trips packed into 5 s steps decide them), so it flipped the advice from look to look on Logan's
    runs and now and then preferred topping up with 2-pointers by the lodge, which the doc's analysis
    puts within its noise of going deep. The program still gives `expect`, the points this plan leads to
    (the held ones and the rest of the run from the lodge)."""
    home = cheb(x, y, *lodge)
    h, kept = len(held), float(sum(held))
    free = HOLD_MAX - h
    tk = kill_s(turned_in)
    best = None      # (trip points, band index, kills_max, time used)
    trips = {}
    for i, (lo, hi, v) in enumerate(BANDS):
        y0, y1 = band_y(BANDS[i], lodge[1])
        inside = y0 <= y <= y1
        travel = RUN_S * (y0 - y if y < y0 else y - y1 if y > y1 else 0)
        back = RUN_S * (max(home, (lo + hi) / 2) if inside else (lo + hi) / 2)
        kmax = int(max(0.0, left_s - travel - back - RETURN_MARGIN_S) // tk) if free else 0
        if kmax < 1:
            continue
        k = min(free, kmax)
        trips[i] = k * v
        if best is None or k * v >= best[0]:
            best = (k * v, i, kmax, travel + k * tk + back)
    if best is None:
        if h:
            return {"phase": "return", "band": None, "zone": None, "kills": 0, "kills_max": 0, "min_points": None,
                    "expect": round(kept + _rest(left_s - RUN_S * home - WARDEN_S, turned_in + h), 1)}
        return {"phase": "done", "band": None, "zone": None, "kills": 0, "kills_max": 0, "min_points": None,
                "expect": 0.0}
    pts, key, kmax, used = best
    k = min(free, kmax)
    lo = hi = key
    while lo - 1 in trips and trips[lo - 1] >= 0.8 * pts:
        lo -= 1
    while hi + 1 in trips and trips[hi + 1] >= 0.8 * pts:
        hi += 1
    zy0, zy1 = band_y(BANDS[lo], lodge[1])[0], band_y(BANDS[hi], lodge[1])[1]
    if kmax < free:
        min_points = 2                   # time, not trophy slots, is short: any calm buck pays
    else:
        min_points = 6 if BANDS[lo][0] >= 160 else 4 if BANDS[lo][0] >= 120 else 2
    return {"phase": "out" if y < zy0 else "north" if y > zy1 else "hunt", "band": key, "zone": [zy0, zy1],
            "zone_points": [BANDS[lo][2], BANDS[hi][2]], "kills": k, "kills_max": kmax, "min_points": min_points,
            "expect": round(kept + pts + _rest(left_s - used - WARDEN_S, turned_in + h + k), 1)}


def _octant(dx: int, dy: int) -> int | None:
    """0 = north .. 7 = northwest, map y down; None on our own tile."""
    if dx == 0 and dy == 0:
        return None
    return round(math.degrees(math.atan2(dx, -dy)) / 45) % 8


def downwind(me, deer, wind: str | None) -> int:
    """The detection penalty from the wind: 30 when the deer is directly downwind of us (the wind blows from us
    to it), 15 when one octant off, else 0 (the forum post's 2026 wind rule)."""
    if wind not in OCTANTS:
        return 0
    o = _octant(deer[0] - me[0], deer[1] - me[1])
    if o is None:
        return 0
    off = min((o - OCTANTS.index(wind)) % 8, (OCTANTS.index(wind) - o) % 8)
    return 30 if off == 0 else 15 if off == 1 else 0


class GroundsLog:
    """One session's Hunting Grounds run, from the world model's events (module docstring)."""

    def __init__(self):
        self.run: dict | None = None
        self.moods: dict[int, tuple[int, str | None, float]] = {}   # serial -> (points, mood, when)

    def feed(self, events, now: float, state) -> None:
        for e in events:
            ev = e.get("ev")
            if ev == "speech_heard":
                text = e.get("text") or ""
                if text == START_TEXT:
                    self.run = {"started": now, "ends": now + SESSION_S, "held": [], "turned_in": 0,
                                "score": 0, "ammo": None, "wind": None, "lodge": LODGE}
                    self.moods.clear()
                    continue
                r = self.run
                if (m := BUCK.match(e.get("name") or "")) and text in MOODS:
                    self.moods[e["serial"]] = (int(m.group(1)), MOODS[text], now)
                    continue
                if r is None:
                    continue
                if text == EXPIRED_TEXT:
                    self.run = None
                elif m := MINUTES_LEFT.match(text):
                    r["ends"] = now + 60 * int(m.group(1))
                elif m := TROPHY.match(text):
                    r["held"].append(int(m.group(1)))
                elif m := TURNIN.match(text):
                    r["held"] = []
                    r["turned_in"] += int(m.group(1))
                    r["score"] = int(m.group(3))
                    r["ammo"] = 15
                elif m := WIND.match(text):
                    r["wind"] = m.group(1)
            elif ev == "gump_open" and self.run is not None:
                lines = e.get("lines") or []
                for i, line in enumerate(lines):
                    if m := GUMP_LEFT.match(line):
                        self.run["ends"] = now + 60 * int(m.group(1) or 0) + int(m.group(2))
                    elif line == "Ammo" and i + 1 < len(lines) and (m := AMMO.match(lines[i + 1])):
                        self.run["ammo"] = int(m.group(1))
        if len(self.moods) > 600:       # deer that left long ago
            self.moods = {s: v for s, v in self.moods.items() if now - v[2] <= 120}

    def status(self, now: float, state) -> dict | None:
        """The run for the state port (the viz's Hunting Grounds badge and map overlay), or None outside."""
        r = self.run
        if r is None:
            return None
        me = state.self
        gone = me.x is not None and cheb(me.x, me.y, *r["lodge"]) > LEAVE_R and now - r["started"] > END_GRACE_S
        if gone or now > r["ends"] + END_GRACE_S:     # the exit moongate / a recall; no expiry line seen
            self.run = None
            return None
        left = max(0.0, r["ends"] - now)
        lodge = r["lodge"]
        t = tier(r["turned_in"])
        _, wname, rng, graze = TIERS[t]
        nxt = TIERS[t + 1] if t + 1 < len(TIERS) else None
        out = {
            "started": round(r["started"], 3), "ends": round(r["ends"], 3), "left_s": round(left, 1),
            "lodge": list(lodge), "region": list(REGION), "held": list(r["held"]), "held_points": sum(r["held"]),
            "hold_max": HOLD_MAX, "turned_in": r["turned_in"], "score": r["score"], "ammo": r["ammo"],
            "wind": r["wind"],
            "weapon": {"name": wname, "range": rng, "graze": graze,
                       "shoot_from": [NERVOUS_R + 1, min(rng, VIEW_R)],
                       "next": None if nxt is None else {"name": nxt[1], "at": nxt[0], "range": nxt[2], "graze": nxt[3]}},
            "bands": [{"y": list(band_y(b, lodge[1])), "points": b[2]} for b in BANDS],
            "no_return_r": int(max(0.0, left - RETURN_MARGIN_S) / RUN_S),
            "home_s": None, "slack_s": None, "warn_slack_s": WARN_SLACK_S, "plan": None, "bucks": [],
        }
        if me.x is None:
            return out
        home_s = RUN_S * cheb(me.x, me.y, *lodge) + RETURN_MARGIN_S
        out["home_s"] = round(home_s, 1)
        out["slack_s"] = round(left - home_s, 1)
        p = plan(me.x, me.y, left, r["held"], r["turned_in"], lodge)
        out["plan"] = p
        bucks = []
        for serial, (pts, mood, when) in list(self.moods.items()):
            m = state.mobiles.get(serial)
            if m is None or m.x is None:
                continue
            if now - when > STATE_TTL_S:
                mood = None
            if p["phase"] in ("return", "done") or len(r["held"]) >= HOLD_MAX or p["min_points"] is None \
                    or pts < p["min_points"] or mood == "fleeing":
                verdict = "skip"
            elif mood == "calm":
                verdict = "shoot"
            else:
                verdict = "wait"         # nervous (calm again after 10 s alone) or no recent label
            bucks.append({"serial": f"0x{serial:08X}", "x": m.x, "y": m.y, "points": pts, "mood": mood,
                          "dist": cheb(me.x, me.y, m.x, m.y), "verdict": verdict,
                          "downwind": downwind((me.x, me.y), (m.x, m.y), r["wind"])})
        bucks.sort(key=lambda b: (b["verdict"] != "shoot", -b["points"], b["dist"]))
        out["bucks"] = bucks
        return out

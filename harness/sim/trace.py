"""A simulator run's trace: every mobile's state each tick and the combat events, for the viz Sim page (docs/SIM.md
"Traces", docs/VISUALIZER.md).

    run.py --trace [DIR] / fight*.py --trace DIR  ->  <DIR>/<stamp>_<label>_s<seed>.trace.jsonl.gz

One gzipped JSON object per line; every line but the head has `t` (world.t, 0.01 s):
- `head`: label, seed, scenario, created, epoch (the game time of t=0), tick, player serial, runner args, the
  scenario's area and per creature type `specs` (aggro, melee, swing_s, ranged range/keep/every_s/cast_s/flight_s,
  specials' kind/range/radius/far_r);
- `key`: every mobile's record, the world's and the ground items' (at attach and every KEY_EVERY_S);
- `d`: what changed in a tick: `m` {serial: changed fields}, `add` / `rm` mobiles, `w` world fields, `ia` / `ir` items;
- `e`: an event (`e` its name; World._ev, reds, SimLink.intent, Tracer.tee's `log` lines);
- `end`: the run's summary.
`state_at(read(path), t)` rebuilds the records at `t` from the key at or before it and the deltas after.

The tracer only reads the world: it never draws from its random generators, asks its sight or touches its packet
queue, so a traced run is the untraced one (test_sim test_trace_no_side_effects).
"""
from __future__ import annotations

import datetime
import gzip
import json
import os
import re

DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "logs", "sim_traces")
KEY_EVERY_S = 10.0
_CLOCK = re.compile(r"^\d\d:\d\d:\d\d(?:\.\d+)? ")


def stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def path_for(dir: str, stamp: str, label: str, seed) -> str:
    os.makedirs(dir, exist_ok=True)
    return os.path.join(dir, f"{stamp}_{label}_s{seed}.trace.jsonl.gz")


def _r(v):
    """A time field: 0.01 s, 0 (or None) when inactive."""
    return round(v, 2) if v else 0


def _top(d: dict) -> dict:
    return {str(k): int(round(v)) for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:6]}


def mob_rec(m) -> dict:
    rec = {"s": m.serial, "kind": m.kind, "key": m.key, "name": m.name, "body": m.body, "noto": m.noto,
           "x": m.x, "y": m.y, "z": m.z, "d": m.d, "h": int(m.hits), "hm": int(m.hits_max), "al": m.alive,
           "gh": m.ghost, "w": m.warmode, "tg": m.target, "md": m.mode if m.kind == "pet" else None, "kl": m.kill,
           "cp": m.spec.get("copy_of"), "th": _top(m.threat), "db": _top(m.damage_by), "ns": _r(m.next_swing),
           "cd": {k: _r(v) for k, v in m.cd.items()},
           "cs": [round(m.cast[0], 2), m.cast[1], round(m.cast[2], 2)] if m.cast else None,
           "po": _r(m.poison_until), "bl": _r(m.bleed_until),
           "ds": [[round(d[0] + (d[1] - 1) * d[3], 2), d[2]] for d in m.diseases],
           "dc": _r(m.discord_until), "ca": _r(m.calm_until),
           "pv": [m.provoke[0], _r(m.provoke[1])] if m.provoke else None,
           "hd": _r(m.held_until), "hi": _r(m.hidden_until), "im": _r(m.immune_until), "ex": _r(m.expires),
           "ch": [_r(m.charge_at[0]), m.charge_at[1]] if m.charge_at else None,
           "fd": [_r(m.feed[0]), m.feed[2]] if m.feed else None}
    if m.kind == "player":
        rec.update(st=m.stam, sm=m.stam_max, mn=m.mana, mm=m.mana_max)
    return rec


def world_rec(w) -> dict:
    return {"songs": {k: _r(v) for k, v in w.songs.items()}, "pen": w.penalty, "par": _r(w.paralyzed_until),
            "ham": _r(w.hamstrung_until), "pot": _r(w.potion_ready),
            "rc": round(w.recall_at[0], 2) if w.recall_at else None, "vet": _r(w.vet_end),
            "pvp": round(w.pvp_t, 2) if w.pvp_t > -1e8 else None}


def item_recs(w) -> dict:
    """Ground items (no container, a position) by serial."""
    out = {}
    for s, it in w.items.items():
        if it.get("container") is None and it.get("x") is not None:
            rec = {"s": s, "kind": it.get("kind"), "g": it.get("graphic"), "x": it["x"], "y": it.get("y"),
                   "z": it.get("z"), "name": it.get("name"), "until": it.get("until"), "t": it.get("t"),
                   "noto": it.get("noto")}
            out[s] = {k: (round(v, 2) if k in ("until", "t") and v is not None else v)
                      for k, v in rec.items() if v is not None}
    return out


def _spec(sp: dict) -> dict:
    r = sp.get("ranged")
    return {"aggro": sp.get("aggro_range"), "melee": bool(sp.get("melee")), "swing_s": sp.get("swing_s"),
            "ranged": {k: r.get(k) for k in ("range", "keep", "every_s", "cast_s", "flight_s")} if r else None,
            "specials": [{k: s.get(k) for k in ("kind", "range", "radius", "far_r")} for s in sp.get("specials", [])]}


class _Tee:
    """A text stream writing through to `stream`, each complete line also a `log` event."""

    def __init__(self, tracer, stream):
        self.tracer, self.stream, self.buf = tracer, stream, ""

    def writable(self):
        return True

    def write(self, s):
        self.stream.write(s)
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.tracer.log(line)
        return len(s)

    def flush(self):
        self.stream.flush()

    def finish(self):
        if self.buf:
            self.tracer.log(self.buf)
            self.buf = ""


class Tracer:
    def __init__(self, world, path: str, *, label: str, seed, runner_args=(), epoch: float | None = None):
        self.world, self.path = world, path
        self.f = gzip.open(path, "wt", compresslevel=6, encoding="utf-8", newline="\n")
        self.tees = []
        sc = world.sc
        specs = {k: _spec(c) for k, c in (sc.get("creatures") or {}).items()}
        for p in world.pets():
            specs.setdefault(p.key, _spec(p.spec))
        self._line({"k": "head", "v": 1, "label": label, "seed": seed, "scenario": sc.get("name"),
                    "created": datetime.datetime.now().isoformat(timespec="seconds"), "epoch": epoch,
                    "tick": 0.1, "player": world.player.serial, "runner_args": list(runner_args),
                    "area": sc.get("area"), "specs": specs})
        self._key(world)
        world.trace = self

    def _line(self, obj):
        self.f.write(json.dumps(obj, separators=(",", ":")) + "\n")

    def _key(self, w):
        self.mobs = {s: mob_rec(m) for s, m in w.mobs.items()}
        self.w = world_rec(w)
        self.items = item_recs(w)
        self.key_t = w.t
        self._line({"k": "key", "t": round(w.t, 2), "mobs": list(self.mobs.values()), "w": self.w,
                    "items": list(self.items.values())})

    def event(self, t: float, kind: str, fields: dict):
        self._line({"k": "e", "t": round(t, 2), "e": kind, **fields})

    def log(self, line: str):
        self.event(self.world.t, "log", {"text": _CLOCK.sub("", line, count=1)})

    def tick(self, w):
        if w.t - self.key_t >= KEY_EVERY_S - 1e-9:
            self._key(w)
            return
        d = {}
        mobs = {s: mob_rec(m) for s, m in w.mobs.items()}
        ch = {}
        for s, rec in mobs.items():
            old = self.mobs.get(s)
            if old is None:
                d.setdefault("add", []).append(rec)
            elif old != rec:
                ch[str(s)] = {k: v for k, v in rec.items() if old.get(k) != v}
        if ch:
            d["m"] = ch
        rm = [s for s in self.mobs if s not in mobs]
        if rm:
            d["rm"] = rm
        wr = world_rec(w)
        if wr != self.w:
            d["w"] = {k: v for k, v in wr.items() if self.w.get(k) != v}
        items = item_recs(w)
        ia = [rec for s, rec in items.items() if self.items.get(s) != rec]
        if ia:
            d["ia"] = ia
        ir = [s for s in self.items if s not in items]
        if ir:
            d["ir"] = ir
        self.mobs, self.w, self.items = mobs, wr, items
        if d:
            self._line({"k": "d", "t": round(w.t, 2), **d})

    def tee(self, stream):
        t = _Tee(self, stream)
        self.tees.append(t)
        return t

    def close(self, summary: dict):
        for t in self.tees:
            t.finish()
        self._line({"k": "end", "t": round(self.world.t, 2), "summary": summary})
        self.f.close()
        self.world.trace = None


def read(path: str) -> list[dict]:
    """The trace's lines (a run cut short: what was written)."""
    out = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    out.append(json.loads(line))
    except (EOFError, json.JSONDecodeError):
        pass
    return out


def state_at(lines: list[dict], t: float) -> dict:
    """{"mobs": {serial: rec}, "w": world rec, "items": {serial: rec}} at time `t`."""
    k = None
    for i, ln in enumerate(lines):
        if ln["k"] == "key" and ln["t"] <= t + 1e-9:
            k = i
    if k is None:
        return {"mobs": {}, "w": {}, "items": {}}
    key = lines[k]
    mobs = {r["s"]: dict(r) for r in key["mobs"]}
    w = dict(key["w"])
    items = {r["s"]: dict(r) for r in key["items"]}
    for ln in lines[k + 1:]:
        if ln["k"] == "key" or ln.get("t", 0) > t + 1e-9:
            break
        if ln["k"] != "d":
            continue
        for r in ln.get("add", ()):
            mobs[r["s"]] = dict(r)
        for s, f in (ln.get("m") or {}).items():
            mobs[int(s)].update(f)
        for s in ln.get("rm", ()):
            mobs.pop(s, None)
        w.update(ln.get("w") or {})
        for r in ln.get("ia", ()):
            items[r["s"]] = dict(r)
        for s in ln.get("ir", ()):
            items.pop(s, None)
    return {"mobs": mobs, "w": w, "items": items}

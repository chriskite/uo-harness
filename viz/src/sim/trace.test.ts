import { describe, expect, test } from "bun:test";
import { effectsAt, parseTrace } from "./trace.ts";

const mob = (s: number, kind: string, name: string, x: number, y: number) => ({
  s, kind, key: kind === "monster" ? "banshee" : null, name, body: 400, noto: 3, x, y, z: 0, d: 0, h: 50, hm: 50,
  al: true, gh: false, w: false, tg: null, md: null, th: {}, db: {}, ns: 0, cd: {}, cs: null, po: 0, bl: 0, ds: [],
  dc: 0, ca: 0, pv: null, hd: 0, hi: 0, im: 0, ex: null, ch: null, fd: null,
});

const W = { songs: {}, pen: 0, par: 0, ham: 0, pot: 0, rc: null, vet: 0, pvp: 0 };

const FIXTURE = [
  { k: "head", v: 1, label: "t", seed: 1, scenario: "mausoleum", created: "x", epoch: 0, tick: 0.1, player: 1, runner_args: [], area: null, specs: {} },
  { k: "key", t: 0, mobs: [mob(1, "player", "Logan", 10, 10), mob(2, "monster", "a banshee", 20, 10)], w: W, items: [] },
  { k: "d", t: 0.1, m: { "1": { x: 11, h: 40 } }, add: [mob(3, "pet", "Tank", 12, 10)] },
  { k: "e", t: 1, e: "release", by: 2, v: 1, land: 2, g: 14036, x: 20, y: 10, z: 0, spell: null },
  { k: "d", t: 1.2, m: { "3": { cs: [3, 2, 1.2] } }, rm: [2], w: { pen: 5 }, ia: [{ s: 9, kind: "corpse", x: 20, y: 10 }] },
  { k: "e", t: 1.4, e: "hurt", v: 1, by: 2, n: 7, kind: "spell", h: 33 },
  { k: "end", t: 3, summary: {} },
]
  .map((o) => JSON.stringify(o))
  .join("\n");

describe("parseTrace / stateAt", () => {
  const tr = parseTrace(FIXTURE);
  test("bounds, names and sorted lines", () => {
    expect(tr.t0).toBe(0);
    expect(tr.t1).toBe(3);
    expect(tr.events.map((e) => e.e)).toEqual(["release", "hurt"]);
    expect(tr.names.get(3)).toEqual({ name: "Tank", key: null, kind: "pet" });
    expect(tr.nameOf(2)).toBe("a banshee");
  });
  test("the keyframe alone before the first delta", () => {
    const f = tr.stateAt(0.05);
    expect([...f.mobs.keys()]).toEqual([1, 2]);
    expect(f.mobs.get(1)!.x).toBe(10);
  });
  test("field changes and add", () => {
    const f = tr.stateAt(0.5);
    expect(f.mobs.get(1)!.x).toBe(11);
    expect(f.mobs.get(1)!.h).toBe(40);
    expect(f.mobs.get(3)!.name).toBe("Tank");
  });
  test("rm, world change and items; the parsed records stay untouched", () => {
    const f = tr.stateAt(2);
    expect(f.mobs.has(2)).toBe(false);
    expect(f.mobs.get(3)!.cs).toEqual([3, 2, 1.2]);
    expect(f.w.pen).toBe(5);
    expect(f.items.get(9)!.kind).toBe("corpse");
    expect(tr.keys[0]!.mobs[0]!.x).toBe(10);
    expect(tr.stateAt(0.5).mobs.get(3)!.cs).toBeNull();
  });
  test("eventsIn is inclusive and filtered", () => {
    expect(tr.eventsIn(1, 1.4).length).toBe(2);
    expect(tr.eventsIn(0, 5, (e) => e.e === "hurt").map((e) => e.n)).toEqual([7]);
    expect(tr.lastEvents(5, 1, () => true).map((e) => e.e)).toEqual(["hurt"]);
  });
});

describe("effectsAt", () => {
  const tr = parseTrace(FIXTURE);
  test("a projectile is halfway at (release + land) / 2, toward the victim's tile now", () => {
    const t = 1.5;
    const fx = effectsAt(tr, tr.stateAt(t), t);
    expect(fx.projectiles.length).toBe(1);
    const p = fx.projectiles[0]!;
    expect(p.f).toBeCloseTo(0.5);
    expect(p.x).toBeCloseTo(15.5);
    expect(p.y).toBeCloseTo(10);
  });
  test("none before the release or after landing", () => {
    expect(effectsAt(tr, tr.stateAt(0.9), 0.9).projectiles.length).toBe(0);
    expect(effectsAt(tr, tr.stateAt(2), 2).projectiles.length).toBe(0);
  });
  test("casts from cs, floaters within 1.5 s", () => {
    const fx = effectsAt(tr, tr.stateAt(2.1), 2.1);
    expect(fx.casts.map((c) => [c.by, c.v, c.start, c.end])).toEqual([[3, 2, 1.2, 3]]);
    expect(fx.casts[0]!.f).toBeCloseTo(0.5);
    expect(fx.floaters.map((f) => [f.m, f.n, f.kind])).toEqual([[1, 7, "spell"]]);
    expect(effectsAt(tr, tr.stateAt(3), 3).floaters.length).toBe(0);
  });
});

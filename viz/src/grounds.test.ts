import { describe, expect, test } from "bun:test";
import { buckCaption, groundsView, windAdvice, type GroundsPlan, type GroundsStatus } from "./grounds.ts";

// shapes as harness/hunting_grounds.py GroundsLog.status sends them (Logan's run 2, 2026-11-22)
const status = (over: Partial<GroundsStatus>, plan: Partial<GroundsPlan> | null): GroundsStatus => ({
  started: 1000,
  ends: 1900,
  left_s: 600,
  instance: "east",
  lodge: [8342, 89],
  region: [8218, 72, 8474, 330],
  held: [],
  held_points: 0,
  hold_max: 5,
  turned_in: 5,
  score: 12,
  ammo: 15,
  wind: "south",
  weapon: { name: "Recurve Bow", range: 14, graze: 0.2, shoot_from: [11, 14], next: { name: "Hunting Bow", at: 6, range: 16, graze: 0.15 } },
  bands: [],
  no_return_r: 2900,
  home_s: 40,
  slack_s: 560,
  warn_slack_s: 30,
  plan: plan && {
    phase: "out",
    band: 4,
    zone: [249, 330],
    zone_points: [7.6, 9.1],
    kills: 5,
    kills_max: 15,
    min_points: 6,
    expect: 95.2,
    ...plan,
  },
  bucks: [],
  ...over,
});

describe("groundsView", () => {
  test("heading out: the zone, what to shoot, from where, the weapon and its next tier", () => {
    const v = groundsView(status({}, {}))!;
    expect(v.left).toBe("10:00");
    expect(v.headline).toBe("Run south to y 249–330 (~7.6–9.1 pts a buck)");
    expect(v.lines).toContain("shoot calm bucks ≥ 6 pts");
    expect(v.lines).toContain("shoot from 11–14 tiles");
    expect(v.lines).toContain("Recurve Bow: range 14, graze 20% · Hunting Bow at 6 (1 more turned in)");
    expect(v.lines).toContain("wind to the south: approach deer from their south side");
    expect(v.warn).toBe(false);
    expect(v.urgent).toBe(false);
  });
  test("from the Great Bow on (range at the view's 18): shoot from where you see them", () => {
    const g = status({ weapon: { name: "Great Bow", range: 18, graze: 0.1, shoot_from: [11, 18], next: null } }, { phase: "hunt" });
    const v = groundsView(g)!;
    expect(v.headline).toBe("Hunt here: calm bucks ≥ 6 pts");
    expect(v.lines).toContain("shoot from where you see them (≤ 18 tiles)");
  });
  test("holding trophies: the time to spare before heading back; warn inside 30 s, urgent once it's gone", () => {
    const held = { held: [8, 8, 8], held_points: 24 };
    expect(groundsView(status({ ...held, slack_s: 75 }, { phase: "hunt" }))!.lines).toContain("head back within 1:15 (run home ~40 s)");
    expect(groundsView(status({ ...held, slack_s: 75 }, { phase: "hunt" }))!.warn).toBe(false);
    expect(groundsView(status({ ...held, slack_s: 30 }, { phase: "hunt" }))!.warn).toBe(true);
    const late = groundsView(status({ ...held, slack_s: -3 }, { phase: "return", band: null, zone: null, min_points: null, kills: 0 }))!;
    expect(late.urgent).toBe(true);
    expect(late.headline).toBe("Head back now: 3 trophies (24 pts)");
    expect(late.lines).toContain("head back NOW: the run home takes ~40 s, 600 s left");
  });
  test("five held: run back; nothing outside the Grounds", () => {
    const full = status({ held: [8, 8, 8, 10, 10], held_points: 44, slack_s: 200 }, { phase: "return", band: null, zone: null, min_points: null, kills: 0 });
    expect(groundsView(full)!.headline).toBe("5 trophies: run back to the warden");
    expect(groundsView(full)!.warn).toBe(true);
    expect(groundsView(null)).toBeNull();
    expect(groundsView(undefined)).toBeNull();
  });
});

describe("captions", () => {
  test("a buck: points, mood, the scent penalty when downwind of us", () => {
    const b = { serial: "0x0020C000", x: 1, y: 2, points: 12, dist: 9, verdict: "shoot" as const };
    expect(buckCaption({ ...b, mood: "calm", downwind: 0 })).toBe("12 calm");
    expect(buckCaption({ ...b, mood: null, downwind: 30 })).toBe("12 ? · scent +30%");
    expect(windAdvice(null)).toBeNull();
  });
});

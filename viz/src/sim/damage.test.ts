import { describe, expect, test } from "bun:test";
import { OTHER, binLabels, damageBins } from "./damage.ts";
import { parseTrace } from "./trace.ts";

const rec = (s: number, kind: string, key: string | null, name: string) => ({ s, kind, key, name });

// the player (1), a pet (2), monsters 10..17 of 8 types; monster m hurts the player (m - 9) * 10 each
const mobs = [rec(1, "player", null, "Logan"), rec(2, "pet", null, "Tank"), ...Array.from({ length: 8 }, (_, i) => rec(10 + i, "monster", `m${i + 1}`, `mob ${i + 1}`))];
const lines = [
  { k: "head", v: 1, label: "t", seed: 1, scenario: "s", created: "x", epoch: 0, tick: 0.1, player: 1, runner_args: [], area: null, specs: {} },
  { k: "key", t: 0, mobs, w: {}, items: [] },
  ...mobs.slice(2).map((m, i) => ({ k: "e", t: 1 + i, e: "hurt", v: 1, by: m.s, n: (i + 1) * 10, kind: "melee", h: 100 })),
  { k: "e", t: 12, e: "hurt", v: 1, by: null, n: 3, kind: "bleed", h: 97 },
  { k: "e", t: 13, e: "hurt", v: 10, by: 2, n: 20, kind: "melee", h: 0 },
  { k: "e", t: 14, e: "hurt", v: 11, by: 1, n: 5, kind: "melee", h: 0 },
  { k: "e", t: 14.5, e: "hurt", v: 2, by: 11, n: 9, kind: "melee", h: 0 },
  { k: "end", t: 15, summary: {} },
];
const tr = parseTrace(lines.map((o) => JSON.stringify(o)).join("\n"));

describe("damageBins", () => {
  test("bins of binS over the run, by source, on the victim", () => {
    const bins = damageBins(tr, { victim: 1 }, 5);
    expect(bins.map((b) => b.t)).toEqual([0, 5, 10]);
    // t 1..4 -> bin 0 (m1..m4), t 5..8 -> bin 1 (m5..m8), t 12 bleed -> bin 2
    expect(bins[0]!.by).toEqual({ other: 30, m3: 30, m4: 40 });
  });
  test("top 6 sources keep their label; the rest are 'other'", () => {
    const bins = damageBins(tr, { victim: 1 }, 5);
    const labels = binLabels(bins);
    expect(labels).toEqual(["m8", "m7", "m6", "m5", "m4", "m3", OTHER]);
    // other = m1 10 + m2 20 + bleed 3
    expect(bins.reduce((n, b) => n + (b.by[OTHER] ?? 0), 0)).toBe(33);
    expect(bins[2]!.by).toEqual({ other: 3 });
  });
  test("team damage: the player and pets on monsters only", () => {
    const bins = damageBins(tr, { team: true }, 5);
    expect(bins[2]!.by).toEqual({ Tank: 20, Logan: 5 });
    expect(bins[0]!.by).toEqual({});
  });
});

import { describe, expect, test } from "bun:test";
import type { MiningLeg } from "./jobs.ts";
import { currentOreRate, legOre, metalMix, miningCarried, miningPhase, oreTally, runningMining, spotLegs, tripBound, tripLegs, tripTally } from "./miningjob.ts";
import type { AgentIntent, Item, Snapshot } from "./types.ts";

const ORE_KINDS = ["to_stand", "dig"];
/** A mining step; to_stand/dig carry the leg's ore in their text as loop_mining writes it. */
const step = (kind: string, since: number, until?: number, ore = 0, trip = 1, loop = "mining"): AgentIntent => ({
  text: ORE_KINDS.includes(kind) ? `Mining by 1,1 (${ore} ore)` : kind,
  kind,
  loop,
  trip,
  trips: 2,
  since,
  until,
});
const leg = (trip: number, n: number, outcome: string, t_start: number, t_end: number, ore = 10, smelts = 1): MiningLeg =>
  ({ n: t_start, trip, leg: n, outcome, t_start, t_end, ore, smelts, ingots_made: 2 * ore, spot: `s${n}` }) as unknown as MiningLeg;

describe("runningMining", () => {
  test("a mining step, also between trips, is a running run", () => {
    const dig = step("dig", 1);
    expect(runningMining(dig)).toBe(dig);
    const between = step("trip_done", 1);
    expect(runningMining(between)).toBe(between);
  });
  test("an ended run, another loop or no intent is not", () => {
    for (const k of ["done", "stopped", "break_due"]) expect(runningMining(step(k, 1))).toBeNull();
    expect(runningMining(step("dig", 1, undefined, 0, 1, "lumber"))).toBeNull();
    expect(runningMining(null)).toBeNull();
    expect(runningMining(undefined)).toBeNull();
  });
});

describe("miningPhase", () => {
  test("a kind with a phase decides it", () => {
    expect(miningPhase(step("dig", 1), [])).toBe("dig");
    expect(miningPhase(step("to_forge", 1), undefined)).toBe("smelt");
    expect(miningPhase(step("hop_plan", 1), undefined)).toBe("hop");
    expect(miningPhase(step("store", 1), undefined)).toBe("home");
    expect(miningPhase(step("lockout", 1), undefined)).toBe("lockout");
    expect(miningPhase(step("flee", 1), undefined)).toBe("escape");
  });
  test("an anywhere kind takes the newest earlier step with a phase; out when none has one", () => {
    expect(miningPhase(step("captcha", 3), [step("recall_out", 1), step("dig", 2), step("captcha", 3)])).toBe("dig");
    expect(miningPhase(step("speech", 1), [step("speech", 1)])).toBe("out");
  });
});

describe("oreTally", () => {
  test("the leg's ore from a to_stand or dig text only", () => {
    expect(oreTally({ text: "Heading to the rock at 1202,2374 (23 ore)", kind: "to_stand", since: 0 })).toBe(23);
    expect(oreTally({ text: "Mining by 1213,2398 (153 ore)", kind: "dig", since: 0 })).toBe(153);
    expect(oreTally({ text: "Smelting 40 ore at the forge at 1,1", kind: "smelt", since: 0 })).toBeNull();
    expect(oreTally({ text: "Mining by 1,1", kind: "dig", since: 0 })).toBeNull();
  });
});

describe("tripLegs", () => {
  const legs = [
    leg(1, 1, "stored", 100, 200), // an earlier run's trip 1
    leg(1, 1, "hopped", 300, 400), // a run killed after its first hop (no row for its last leg)
    leg(1, 1, "hopped", 1000, 1500),
    leg(1, 2, "hopped", 1600, 2000),
  ];
  const cur = step("dig", 2100, undefined, 5);
  test("the bound: the newest history entry that isn't this trip's", () => {
    expect(tripBound(cur, [step("goto", 900, 950, 0, 1, "overseer"), step("leave_room", 950, 960), cur])).toBe(900);
    expect(tripBound(cur, [step("trip_done", 800, 990, 0, 0), step("leave_room", 990, 1000), cur])).toBe(800);
    expect(tripBound(cur, [step("leave_room", 950, 960), cur])).toBeNull();
  });
  test("this trip's hopped legs, oldest first, down to leg 1; older runs left out", () => {
    const hist = [step("goto", 900, 950, 0, 1, "overseer"), cur];
    expect(tripLegs(legs, cur, hist).map((l) => l.t_start)).toEqual([1000, 1600]);
    // no bound in the history: the chain stops at leg 1 all the same
    expect(tripLegs(legs, cur, [cur]).map((l) => l.t_start)).toEqual([1000, 1600]);
  });
  test("a leg that didn't hop is the newest only once the trip is done", () => {
    const stored = [...legs, leg(1, 3, "stored", 2100, 2500)];
    expect(tripLegs(stored, step("trip_done", 2500), []).map((l) => l.leg)).toEqual([1, 2, 3]);
    // the run after it: its trip 1 has no finished leg yet
    expect(tripLegs(stored, step("dig", 2600), [step("dig", 2600)])).toEqual([]);
  });
  test("a gap in the leg numbers, another trip or no trip number ends the chain", () => {
    expect(tripLegs([leg(1, 1, "hopped", 10, 20), leg(1, 3, "hopped", 30, 40)], cur, []).map((l) => l.leg)).toEqual([3]);
    expect(tripLegs(legs, step("dig", 2100, undefined, 0, 2), [])).toEqual([]);
    const { trip: _, ...bare } = cur;
    expect(tripLegs(legs, bare, [])).toEqual([]);
  });
});

describe("legOre", () => {
  const hist = [step("to_stand", 100, 110, 0), step("dig", 110, 200, 20), step("to_forge", 200, 210), step("smelt", 210)];
  test("the current text's tally, else the newest earlier one of this leg", () => {
    expect(legOre(step("dig", 300, undefined, 25), hist, null)).toBe(25);
    expect(legOre(hist[3]!, hist.slice(0, 3), null)).toBe(20);
  });
  test("after a hop only the new leg's steps count; 0 before its first dig", () => {
    const hop = [...hist.slice(0, 2), step("hop", 250, 260), step("recall_out", 260, 270)];
    expect(legOre(step("lockout", 270), hop, 240)).toBe(0);
    expect(legOre(step("dig", 280, undefined, 3), hop, 240)).toBe(3);
  });
  test("an earlier trip's tally isn't this trip's", () => {
    expect(legOre(step("recall_out", 300, undefined, 0, 2), [step("dig", 100, 200, 50, 1)], null)).toBe(0);
  });
});

describe("tripTally", () => {
  const done = [leg(1, 1, "hopped", 0, 10, 100, 2), leg(1, 2, "hopped", 20, 30, 50, 1)];
  const open = { since: 30, stands: 3, smelts: 1, ingots_made: 40 };
  test("finished legs plus the leg in progress", () => {
    expect(tripTally(done, { ore: 25 }, open)).toEqual({ ore: 175, smelts: 4, ingotsMade: 340 });
  });
  test("the leg in progress's smelts are unknown without open_leg; a done trip needs none", () => {
    expect(tripTally(done, { ore: 25 }, undefined)).toEqual({ ore: 175, smelts: null, ingotsMade: null });
    expect(tripTally(done, null, undefined)).toEqual({ ore: 150, smelts: 3, ingotsMade: 300 });
    expect(tripTally([], { ore: 0 }, open)).toEqual({ ore: 0, smelts: 1, ingotsMade: 40 });
  });
});

describe("spotLegs", () => {
  test("the legs at the spot, newest first, a leg without a start last", () => {
    const at = (n: number, spot: string, t_start: number | null) => ({ n, spot, t_start }) as unknown as MiningLeg;
    const legs = [at(1, "a", 100), at(2, "b", 200), at(3, "a", null), at(4, "a", 300), at(5, "a", 50)];
    expect(spotLegs(legs, "a").map((l) => l.n)).toEqual([4, 1, 5, 3]);
    expect(spotLegs(legs, "c")).toEqual([]);
  });
});

describe("metalMix", () => {
  const made = (metals_made: Record<string, number>) => ({ metals_made }) as unknown as MiningLeg;
  test("the legs' ingots made summed per metal, most first, with their share; zeros dropped", () => {
    expect(metalMix([made({ iron: 90, gold: 10, copper: 0 }), made({ iron: 10 })])).toEqual([
      { name: "iron", ingots: 100, share: 100 / 110 },
      { name: "gold", ingots: 10, share: 10 / 110 },
    ]);
  });
  test("ties by name; no legs, no mix", () => {
    expect(metalMix([made({ verite: 5, agapite: 5 })]).map((m) => m.name)).toEqual(["agapite", "verite"]);
    expect(metalMix([])).toEqual([]);
  });
});

describe("currentOreRate", () => {
  // the lockout ends at 1000 with 0 ore (the field run starts there), then 20 ore a stand; a smelt at 1240-1260
  const hist = [
    step("recall_out", 900, 940),
    step("to_stand", 940, 942, 0),
    step("dig", 942, 943, 0),
    step("lockout", 943, 1000),
    step("dig", 1000, 1120, 20),
    step("to_stand", 1120, 1130, 20),
    step("dig", 1130, 1240, 40),
    step("to_forge", 1240, 1250),
    step("smelt", 1250, 1260),
    step("dig", 1260, 1360, 60),
  ];
  const cur = step("dig", 1370, undefined, 70);
  test("ore since the tally of 5 min ago, over the time since then", () => {
    // newest sample at or before 1400 - 300 = 1100: the lockout's end (1000, 0 ore); 70 ore over 400 s
    const r = currentOreRate(cur, [...hist, cur], 1400);
    expect(r?.spanS).toBe(400);
    expect(r?.oreH).toBeCloseTo((70 / 400) * 3600);
    // later: the window starts at the dig that ended at 1240 (40 ore): 30 ore over 300 s
    expect(currentOreRate(cur, [...hist, cur], 1540)).toEqual({ oreH: 360, spanS: 300 });
  });
  test("smelting is field time: the tally carries over from the last dig", () => {
    const smelt = step("smelt", 1250);
    // 40 ore over 1300 - 1000 s
    const r = currentOreRate(smelt, [...hist.slice(0, 8), smelt], 1300);
    expect(r?.spanS).toBe(300);
    expect(r?.oreH).toBeCloseTo(480);
  });
  test("null away from the spot, during the lockout, or under a minute in", () => {
    const home = step("recall_home", 1400);
    expect(currentOreRate(home, [...hist, home], 1500)).toBeNull();
    expect(currentOreRate(step("lockout", 943), hist.slice(0, 3), 990)).toBeNull();
    expect(currentOreRate(step("dig", 1000, undefined, 5), hist.slice(0, 4), 1030)).toBeNull();
    expect(currentOreRate(cur, [...hist, cur], null)).toBeNull();
  });
  test("a new leg starts over at 0, another trip starts the samples over", () => {
    const hop = [...hist, step("hop_plan", 1360, 1380), step("hop", 1380, 1385), step("recall_out", 1385, 1990), step("to_stand", 1990, 2000, 0)];
    const next = step("dig", 2000, undefined, 5);
    expect(currentOreRate(next, [...hop, next], 2030)).toBeNull(); // 40 s since the recall's end
    expect(currentOreRate(next, [...hop, next], 2100)?.oreH).toBeCloseTo((5 / 110) * 3600);
    const trip2 = step("dig", 1370, undefined, 70, 2);
    expect(currentOreRate(trip2, [...hist, trip2], 1400)).toBeNull();
  });
});

describe("miningCarried", () => {
  const ME = "0x00000001";
  const item = (graphic: number, container?: string, amount?: number, layer?: number): Item => ({ graphic, container, amount, layer });
  const world = (items: Record<string, Item>) => ({ self: { serial: ME }, items }) as unknown as Snapshot;
  const items: Record<string, Item> = {
    "0x00000010": item(0x0e75, ME, undefined, 0x15), // the backpack
    "0x00000011": item(0x0e76, "0x00000010"), // a bag in it
    "0x00000012": item(0x0e79, "0x00000011"), // a trapped pouch in the bag
    "0x00000013": item(0x1bf2, "0x00000012", 568),
    "0x00000014": item(0x1bf2, "0x00000010", 10),
    "0x00000015": item(0x19b9, "0x00000010", 4),
    "0x00000016": item(0x19b7, "0x00000011", 6),
    "0x00000017": item(0x0e86, ME, undefined, 0x01), // in hand
    "0x00000018": item(0x0e86, "0x00000010"),
    "0x00000019": item(0x0e85, "0x00000011"),
    "0x0000001A": item(0x19b9, undefined, 9), // on the ground
    "0x0000001B": item(0x1bf2, "0x00000002", 50), // someone else's
    "0x0000001C": item(0x1bf2, "0x0000001D", 7), // a container loop off the pack
    "0x0000001D": item(0x0e76, "0x0000001C"),
  };
  test("ore and ingots at any depth in the pack, pickaxes worn and packed", () => {
    expect(miningCarried(world(items))).toEqual({ ore: 10, ingots: 578, pickaxesWorn: 1, pickaxesPacked: 2 });
  });
  test("no backpack: only the worn pickaxe; no serial: null", () => {
    const { "0x00000010": _, ...nopack } = items;
    expect(miningCarried(world(nopack))).toEqual({ ore: 0, ingots: 0, pickaxesWorn: 1, pickaxesPacked: 0 });
    expect(miningCarried({ self: {}, items } as unknown as Snapshot)).toBeNull();
    expect(miningCarried(null)).toBeNull();
  });
});

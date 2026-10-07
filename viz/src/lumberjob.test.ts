import { describe, expect, test } from "bun:test";
import type { JobTrip } from "./jobs.ts";
import { currentRate, lumberLeg, runningLumber, spotTrips, sumWoods, woodMix } from "./lumberjob.ts";
import type { AgentIntent } from "./types.ts";

const intent = (kind: string, loop = "lumber"): AgentIntent => ({ text: kind, kind, loop, trip: 1, trips: 2, since: 1000 });
const trip = (spot: string, t_start: number | null, woods: Record<string, number>) =>
  ({ spot, t_start, woods }) as unknown as JobTrip;

describe("runningLumber", () => {
  test("a lumber step, also between trips, is a running run", () => {
    const chop = intent("chop");
    expect(runningLumber(chop)).toBe(chop);
    const between = intent("trip_done");
    expect(runningLumber(between)).toBe(between);
  });
  test("an ended run, another loop or no intent is not", () => {
    for (const k of ["done", "stopped", "break_due"]) expect(runningLumber(intent(k))).toBeNull();
    expect(runningLumber(intent("chop", "hunt"))).toBeNull();
    expect(runningLumber(null)).toBeNull();
    expect(runningLumber(undefined)).toBeNull();
  });
});

describe("lumberLeg", () => {
  test("a kind with a leg decides it", () => {
    expect(lumberLeg(intent("chop"), [])).toBe("grove");
    expect(lumberLeg(intent("recall_out"), undefined)).toBe("out");
  });
  test("an anywhere kind takes the newest earlier step with a leg", () => {
    const hist = [intent("recall_out"), intent("chop"), intent("captcha")];
    expect(lumberLeg(intent("captcha"), hist)).toBe("grove");
    expect(lumberLeg(intent("speech"), [intent("speech")])).toBe("out");
  });
});

describe("spot history", () => {
  const trips = [trip("a", 1, { ordinary: 10, oak: 5 }), trip("b", 2, { ash: 9 }), trip("a", 3, { ordinary: 5 })];
  test("trips at the spot, newest first, and their wood mix", () => {
    const here = spotTrips(trips, "a");
    expect(here.map((t) => t.t_start)).toEqual([3, 1]);
    expect(woodMix(sumWoods(here))).toEqual([
      { name: "ordinary", logs: 15, share: 0.75 },
      { name: "oak", logs: 5, share: 0.25 },
    ]);
  });
  test("woods without logs are left out", () => {
    expect(woodMix({ oak: 0 })).toEqual([]);
  });
});

describe("currentRate", () => {
  const step = (kind: string, since: number, until: number | undefined, logs: number, trip = 1): AgentIntent => ({
    text: kind, kind, loop: "lumber", trip, trips: 2, since, until, woods: { ordinary: logs },
  });
  // lockout ends at 1000 (field run starts with 0 logs), then a tree every 120 s, 20 logs each
  const hist = [
    step("recall_out", 900, 940, 0),
    step("lockout", 940, 1000, 0),
    step("to_tree", 1000, 1010, 0),
    step("chop", 1010, 1120, 20),
    step("to_tree", 1120, 1130, 20),
    step("chop", 1130, 1240, 40),
    step("to_tree", 1240, 1250, 40),
    step("chop", 1250, 1360, 60),
  ];
  const cur = step("chop", 1370, undefined, 70);
  test("logs since the tally of 5 min ago, over the time since then", () => {
    // newest sample at or before 1400 - 300 = 1100: to_tree until 1010 (0 logs); 70 logs over 390 s
    const r = currentRate(cur, [...hist, cur], 1400);
    expect(r?.spanS).toBe(390);
    expect(r?.logsH).toBeCloseTo((70 / 390) * 3600);
    // later: the window starts at the chop that ended at 1240 (40 logs): 30 logs over 300 s
    expect(currentRate(cur, [...hist, cur], 1540)).toEqual({ logsH: 360, spanS: 300 });
  });
  test("the field run starts at the lockout's end, not before", () => {
    // a 1000 s window would reach back to 400, but samples before the lockout's end (1000) don't count
    expect(currentRate(cur, [...hist, cur], 1400, 1000)?.spanS).toBe(400);
  });
  test("null away from the grove, during the lockout, without a tally, or under a minute in", () => {
    const home = step("recall_home", 1400, undefined, 70);
    expect(currentRate(home, [...hist, home], 1500)).toBeNull();
    expect(currentRate(step("lockout", 940, undefined, 0), hist.slice(0, 1), 990)).toBeNull();
    const { woods: _, ...bare } = cur;
    expect(currentRate(bare, [...hist, bare], 1400)).toBeNull();
    expect(currentRate(step("to_tree", 1000, undefined, 0), hist.slice(0, 2), 1030)).toBeNull();
  });
  test("a drop in the tally (a theft) and another trip start the samples over", () => {
    const robbed = step("chop", 1370, undefined, 10);
    expect(currentRate(robbed, [...hist, robbed], 1400)).toBeNull();
    const next = step("chop", 1370, undefined, 70, 2);
    expect(currentRate(next, [...hist, next], 1400)).toBeNull();
  });
});

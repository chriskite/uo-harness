import { describe, expect, test } from "bun:test";
import type { JobTrip } from "./jobs.ts";
import { lumberLeg, runningLumber, spotTrips, sumWoods, woodMix } from "./lumberjob.ts";
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

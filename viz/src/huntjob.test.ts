import { describe, expect, test } from "bun:test";
import { DAMAGE_KEEP_S, damageRates, damageSide, foldHunt, runningHunt, tallyRate, TALLY_KEEP_S, type HuntStream } from "./huntjob.ts";
import type { AgentIntent, EventEnvelope, Mobile, Snapshot } from "./types.ts";

const SELF = "0x00104A60";
const empty = (): HuntStream => ({ damage: [], tally: [], from: null });
const hunt = (gold: number, kills: number, started = 1000, kind = "fight"): AgentIntent => ({
  text: kind, kind, loop: "hunt", since: started, started, tally: { gold, kills },
});
let seq = 0;
const intentEv = (t: number, intent: AgentIntent | null): EventEnvelope => ({
  seq: seq++, t, origin: "proxy", data: { ev: "agent_intent", intent },
});
const speech = (t: number, serial: number, text: string): EventEnvelope => ({
  seq: seq++, t, origin: "world", data: { ev: "speech_heard", serial, text, type: 0 },
});
const world = (mobiles: Record<string, Mobile>, last_seen: Snapshot["last_seen"] = {}): Snapshot =>
  ({ self: { serial: SELF }, mobiles, last_seen }) as unknown as Snapshot;

describe("runningHunt", () => {
  test("a hunt step is a running run; done, stopped, another loop or none is not", () => {
    const f = hunt(0, 0);
    expect(runningHunt(f)).toBe(f);
    for (const k of ["done", "stopped"]) expect(runningHunt(hunt(0, 0, 1000, k))).toBeNull();
    expect(runningHunt({ ...f, loop: "lumber" })).toBeNull();
    expect(runningHunt(null)).toBeNull();
  });
});

describe("foldHunt", () => {
  test("keeps damage numbers only, and drops them past DAMAGE_KEEP_S", () => {
    const h = empty();
    for (const e of [speech(100, 0x7b7fd, "-25"), speech(101, 0x7b7fd, "+18"), speech(102, 0x7b7fd, "Kal Ort Por"), speech(103, 0x10, "-0x")])
      foldHunt(h, e);
    expect(h.damage).toEqual([{ t: 100, serial: "0x0007B7FD", amount: 25 }]);
    foldHunt(h, speech(100 + DAMAGE_KEEP_S + 1, 0x10, "-7"));
    expect(h.damage.map((d) => d.amount)).toEqual([7]);
    expect(h.from).toBe(100);
  });
  test("keeps a tally sample per change of gold, kills or run, and the one before the keep window", () => {
    const h = empty();
    foldHunt(h, intentEv(1000, hunt(0, 0)));
    foldHunt(h, intentEv(1001, hunt(0, 0, 1000, "loot")));
    foldHunt(h, intentEv(1002, hunt(500, 1)));
    foldHunt(h, intentEv(1003, { ...hunt(500, 1), loop: "lumber" }));
    foldHunt(h, intentEv(1004, null));
    expect(h.tally.map((s) => [s.t, s.gold])).toEqual([[1000, 0], [1002, 500]]);
    foldHunt(h, intentEv(1002 + TALLY_KEEP_S + 5, hunt(800, 2)));
    expect(h.tally.map((s) => s.t)).toEqual([1002, 1002 + TALLY_KEEP_S + 5]);
  });
});

describe("tallyRate", () => {
  const samples = [
    { t: 1000, started: 1000, gold: 0, kills: 0 },
    { t: 1200, started: 1000, gold: 600, kills: 1 },
    { t: 1500, started: 1000, gold: 1500, kills: 2 },
  ];
  test("a window reaching back past the run's start starts at 0 then", () => {
    expect(tallyRate(samples, [], hunt(1500, 2), "gold", 1200)).toEqual({ perH: (1500 / 200) * 3600, spanS: 200 });
  });
  test("otherwise from the value the tally had at the window's start", () => {
    // at 1300 (1600 - 300) the tally was the 1200 sample's 600
    expect(tallyRate(samples, [], hunt(1500, 2), "gold", 1600)).toEqual({ perH: (900 / 300) * 3600, spanS: 300 });
    expect(tallyRate(samples, [], hunt(1500, 2), "kills", 1600)!.perH).toBe(12);
  });
  test("samples that begin inside the window (page loaded mid-run) start it at the first", () => {
    expect(tallyRate(samples.slice(2), undefined, hunt(1500, 2), "gold", 1600)).toEqual({ perH: 0, spanS: 100 });
  });
  test("before the fold's first sample, a closed history step's tally at its until starts the window", () => {
    const step = (until: number, gold: number, started = 1000): AgentIntent => ({ ...hunt(gold, 0, started), until });
    // steps closed at 1250 (600) and 1290 (700); the fold's samples begin at 1500
    const hist = [step(1250, 600), step(1290, 700), step(1295, 9999, 7), step(1550, 1500)];
    expect(tallyRate(samples.slice(2), hist, hunt(1500, 2), "gold", 1600)).toEqual({ perH: (800 / 310) * 3600, spanS: 310 });
    // none at or before the window's start: the earliest one starts it
    expect(tallyRate(samples.slice(2), hist.slice(1), hunt(1500, 2), "gold", 1550)).toEqual({ perH: (800 / 260) * 3600, spanS: 260 });
  });
  test("another run's samples don't count; too short, no tally or no clock gives null", () => {
    expect(tallyRate(samples, [], hunt(0, 0, 5000), "gold", 5030)).toBeNull();
    expect(tallyRate(samples.map((s) => ({ ...s, started: 1 })), [], hunt(1500, 2), "gold", 1600)).toBeNull();
    expect(tallyRate(samples, [], { ...hunt(0, 0), tally: undefined }, "gold", 1600)).toBeNull();
    expect(tallyRate(samples, [], hunt(1500, 2), "gold", null)).toBeNull();
  });
});

describe("damageSide", () => {
  const w = world(
    {
      "0x0007B7FD": { name: "a shade wolf", graphic: 23, notoriety: 3 },
      "0x000DDEFE": { name: "GraveRobber", graphic: 387, notoriety: 2 },
      "0x00078C2D": { name: "tormented soul", graphic: 401, notoriety: 3, flags: 0 },
      "0x00099999": { name: "Bastet", graphic: 400, notoriety: 6, flags: 0x20 },
      "0x00011111": { name: "someone's horse", graphic: 0xcc, notoriety: 1 },
    },
    { "0x00055555": { name: "a banshee", graphic: 310, notoriety: 3, t: 1, facet: 0, why: "dead" } },
  );
  test("us and green mobiles take damage; hostile creatures, a dead one too, are dealt to", () => {
    expect(damageSide(SELF, w)).toBe("taken");
    expect(damageSide("0x000DDEFE", w)).toBe("taken");
    expect(damageSide("0x0007B7FD", w)).toBe("dealt");
    expect(damageSide("0x00055555", w)).toBe("dealt");
    expect(damageSide("0x00078C2D", w)).toBe("dealt"); // a human body without the player flag is a creature
  });
  test("players, innocents and unknown mobiles are neither", () => {
    expect(damageSide("0x00099999", w)).toBeNull();
    expect(damageSide("0x00011111", w)).toBeNull();
    expect(damageSide("0x00000001", w)).toBeNull();
  });
});

describe("damageRates", () => {
  const w = world({ "0x0007B7FD": { graphic: 23, notoriety: 3 }, "0x000DDEFE": { graphic: 387, notoriety: 2 } });
  const stream: HuntStream = {
    from: 900,
    tally: [],
    damage: [
      { t: 980, serial: "0x0007B7FD", amount: 999 }, // before the window
      { t: 990, serial: "0x0007B7FD", amount: 100 },
      { t: 995, serial: "0x0007B7FD", amount: 50 },
      { t: 999, serial: "0x000DDEFE", amount: 30 },
    ],
  };
  test("damage per second over the last DPS_WINDOW_S, by side", () => {
    expect(damageRates(stream, w, 1000)).toEqual({ dealt: 10, taken: 2, spanS: 15 });
  });
  test("a history shorter than the window measures over what it has; under DPS_MIN_S it is null", () => {
    expect(damageRates({ ...stream, from: 990 }, w, 1000)).toEqual({ dealt: 5, taken: 3, spanS: 10 });
    expect(damageRates({ ...stream, from: 997 }, w, 1000)).toBeNull();
    expect(damageRates(stream, w, null)).toBeNull();
  });
});

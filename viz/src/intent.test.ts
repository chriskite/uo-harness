import { describe, expect, test } from "bun:test";
import { fmtAgo, hpColor, hpFraction, intentClock, intentGoal, intentView, recentIntents } from "./intent.ts";
import type { AgentIntent, EventEnvelope, Snapshot, StateResponse } from "./types.ts";

const chop: AgentIntent = {
  text: "Chopping tree at 1925,2580 (10/15 logs)",
  kind: "chop",
  target: [1925, 2580],
  loop: "lumber",
  trip: 1,
  trips: 3,
  since: 1000,
};

const ev = (t: number): EventEnvelope => ({ seq: 0, t, origin: "proxy", data: { ev: "step" } });

describe("intentClock", () => {
  test("live ages against the wall clock", () => {
    const st = { viz: { mode: "live" } } as unknown as StateResponse;
    expect(intentClock(st, [ev(5)], 2000)).toBe(2000);
  });
  test("replay ages against the replay's newest event, not the wall clock", () => {
    const st = { viz: { mode: "replay" } } as unknown as StateResponse;
    expect(intentClock(st, [ev(1030), ev(1042)], 9_999_999)).toBe(1042);
    expect(intentClock(st, [], 9_999_999)).toBeNull();
  });
});

describe("intentView", () => {
  test("none reported", () => {
    expect(intentView(null, 0)).toBeNull();
    expect(intentView(undefined, 0)).toBeNull();
  });
  test("text, trip context, age and target; working steps spin", () => {
    expect(intentView(chop, 1042)).toEqual({
      text: chop.text,
      kind: "chop",
      tone: "info",
      activity: "spin",
      context: "lumber · trip 1/3",
      age: "for 0:42",
      target: "1925,2580",
    });
  });
  test("waiting on the human or a timer waits; stops and finishes don't spin", () => {
    expect(intentView({ ...chop, kind: "captcha" }, 1000)).toMatchObject({ tone: "warn", activity: "wait" });
    expect(intentView({ ...chop, kind: "lockout" }, 1000)?.activity).toBe("wait");
    expect(intentView({ ...chop, kind: "stopped" }, 1000)).toMatchObject({ tone: "bad", activity: "idle" });
    expect(intentView({ ...chop, kind: "done" }, 1000)?.activity).toBe("idle");
  });
  test("minimal intent: no kind, context, target or clock", () => {
    const v = intentView({ text: "Looking for a banker", since: 5 }, null);
    expect(v).toEqual({
      text: "Looking for a banker",
      kind: "doing",
      tone: "info",
      activity: "spin",
      context: null,
      age: null,
      target: null,
    });
  });
});

describe("fmtAgo", () => {
  test("seconds, minutes, hours", () => {
    expect(fmtAgo(6.9)).toBe("6s ago");
    expect(fmtAgo(59)).toBe("59s ago");
    expect(fmtAgo(60)).toBe("1m ago");
    expect(fmtAgo(3599)).toBe("59m ago");
    expect(fmtAgo(3600)).toBe("1h ago");
    expect(fmtAgo(7500)).toBe("2h 5m ago");
    expect(fmtAgo(-3)).toBe("0s ago");
  });
});

describe("recentIntents", () => {
  const history: AgentIntent[] = [
    { text: "Heading to tree at 1925,2580 (0/15 logs)", kind: "to_tree", since: 900, until: 958 },
    { text: "Chopping tree at 1925,2580 (15/15 logs)", kind: "chop", since: 958, until: 1000 },
    { text: "Waiting for you to solve the captcha", kind: "captcha", since: 1000, until: 1012 },
    { text: "Making boards from 15 logs", kind: "convert", since: 1012 },
  ];
  test("finished steps newest first with ended-ago and duration; the open current step is left out", () => {
    const rows = recentIntents(history, 1018);
    expect(rows.map((r) => [r.kind, r.ago, r.took])).toEqual([
      ["captcha", "6s ago", "0:12"],
      ["chop", "18s ago", "0:42"],
      ["to_tree", "1m ago", "0:58"],
    ]);
    expect(rows[0]!.tone).toBe("warn");
  });
  test("limit, no clock, no history", () => {
    expect(recentIntents(history, 1018, 2).map((r) => r.kind)).toEqual(["captcha", "chop"]);
    expect(recentIntents(history, null)[0]!.ago).toBeNull();
    expect(recentIntents(undefined, 1)).toEqual([]);
  });
  test("a cleared intent (last entry closed) shows up as recent", () => {
    const cleared = [...history.slice(0, 3), { ...history[3]!, until: 1020 }];
    expect(recentIntents(cleared, 1020)[0]).toMatchObject({ kind: "convert", ago: "0s ago", took: "0:08" });
  });
});

describe("intentGoal (map marker)", () => {
  const world = { mobiles: { "0x00000010": { x: 7, y: 8 } }, items: { "0x40000001": { x: 1, y: 2 } } } as unknown as Snapshot;
  test("follows the target serial's current position (a moving mob), items too", () => {
    const attack: AgentIntent = { text: "Attacking a mongbat", kind: "attack", target: [1, 1], target_serial: "0x00000010", since: 0 };
    expect(intentGoal(attack, world)).toEqual([7, 8]);
    expect(intentGoal({ ...attack, target_serial: "0x40000001" }, world)).toEqual([1, 2]);
  });
  test("falls back to the fixed tile when the entity is gone, or none", () => {
    expect(intentGoal({ text: "x", target: [3, 4], target_serial: "0x00000099", since: 0 }, world)).toEqual([3, 4]);
    expect(intentGoal({ text: "x", since: 0 }, world)).toBeNull();
    expect(intentGoal(null, world)).toBeNull();
  });
  test("fights are red; a finished walk doesn't spin", () => {
    expect(intentView({ text: "Attacking", kind: "attack", since: 0 }, 0)).toMatchObject({ tone: "bad", activity: "spin" });
    expect(intentView({ text: "Arrived at x", kind: "arrived", since: 0 }, 0)).toMatchObject({ tone: "ok", activity: "idle" });
  });
});

describe("healthbars", () => {
  test("fraction clamps and needs a max", () => {
    expect(hpFraction(50, 100)).toBe(0.5);
    expect(hpFraction(120, 100)).toBe(1);
    expect(hpFraction(5, 0)).toBeNull();
    expect(hpFraction(undefined, 100)).toBeNull();
  });
  test("game colours: green, yellow below half, red below a quarter", () => {
    expect([hpColor(0.9), hpColor(0.49), hpColor(0.2)]).toEqual(["#22c55e", "#eab308", "#ef4444"]);
  });
});

import { describe, expect, test } from "bun:test";
import { intentClock, intentView } from "./intent.ts";
import type { AgentIntent, EventEnvelope, StateResponse } from "./types.ts";

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
  test("text, trip context, age and target", () => {
    expect(intentView(chop, 1042)).toEqual({
      text: chop.text,
      kind: "chop",
      tone: "info",
      context: "lumber · trip 1/3",
      age: "for 0:42",
      target: "1925,2580",
    });
  });
  test("waiting on the human and stops stand out", () => {
    expect(intentView({ ...chop, kind: "captcha" }, 1000)?.tone).toBe("warn");
    expect(intentView({ ...chop, kind: "stopped" }, 1000)?.tone).toBe("bad");
  });
  test("minimal intent: no kind, context, target or clock", () => {
    const v = intentView({ text: "Looking for a banker", since: 5 }, null);
    expect(v).toEqual({ text: "Looking for a banker", kind: "doing", tone: "info", context: null, age: null, target: null });
  });
});

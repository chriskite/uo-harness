import { describe, expect, test } from "bun:test";
import { gateControls } from "./gate.ts";
import type { Gate } from "./types.ts";

const base: Gate = {
  state: "running",
  blocked: false,
  reason: null,
  paused: false,
  killed: false,
  break_until: null,
  next_break_in_s: 5400,
  active_today_s: 3600,
  daily_cap_s: 28800,
  daily_remaining_s: 25200,
  day: "2026-09-29",
  now: 1_790_000_000,
};

describe("gateControls", () => {
  test("replay or no gate: no controls", () => {
    expect(gateControls(base, false)).toBeNull();
    expect(gateControls(undefined, true)).toBeNull();
    expect(gateControls(null, true)).toBeNull();
  });

  test("running offers pause and kill", () => {
    expect(gateControls(base, true)).toEqual({ toggle: "pause", toggleEnabled: true, killEnabled: true });
  });

  test("manual pause turns the toggle into resume", () => {
    const g = { ...base, state: "paused" as const, blocked: true, paused: true, reason: "agent paused" };
    expect(gateControls(g, true)).toEqual({ toggle: "resume", toggleEnabled: true, killEnabled: true });
  });

  test("break and daily cap cannot be resumed away, but pause and kill still apply", () => {
    for (const state of ["break", "budget_exhausted"] as const) {
      const g = { ...base, state, blocked: true, reason: state };
      expect(gateControls(g, true)).toEqual({ toggle: "pause", toggleEnabled: true, killEnabled: true });
    }
  });

  test("killed disables everything, even with a pause set (resume errors, rearm is CLI-only)", () => {
    const g = { ...base, state: "killed" as const, blocked: true, killed: true, paused: true };
    expect(gateControls(g, true)).toEqual({ toggle: "resume", toggleEnabled: false, killEnabled: false });
  });

  test("a request in flight disables both buttons", () => {
    expect(gateControls(base, true, true)).toEqual({ toggle: "pause", toggleEnabled: false, killEnabled: false });
  });
});

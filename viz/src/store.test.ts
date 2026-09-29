import { describe, expect, test } from "bun:test";
import { cloneFixture } from "./fixtures/fixture.ts";
import { VizStore } from "./store.ts";
import type { Gate, StateResponse } from "./types.ts";

const gate = (state: Gate["state"], now: number): Gate => ({
  state,
  blocked: state !== "running",
  reason: state === "running" ? null : `agent ${state}`,
  paused: state === "paused",
  killed: false,
  break_until: null,
  next_break_in_s: 3000,
  active_today_s: 600,
  daily_cap_s: 28800,
  daily_remaining_s: 28200,
  day: "2026-09-29",
  now,
});

const withGate = (g: Gate): StateResponse => {
  const s = cloneFixture();
  delete s.events;
  return { ...s, gate: g };
};

describe("VizStore gate", () => {
  test("a button reply's gate is not undone by an older state frame, only by a newer one", () => {
    const store = new VizStore((flush) => flush());
    store.setState(withGate(gate("running", 100)));
    store.setGate(gate("paused", 101));
    expect(store.getSnapshot().state?.gate?.state).toBe("paused");

    store.setState(withGate(gate("running", 100.5))); // polled before the pause landed
    expect(store.getSnapshot().state?.gate?.state).toBe("paused");

    store.setState(withGate(gate("running", 102))); // resumed elsewhere (CLI) since
    expect(store.getSnapshot().state?.gate?.state).toBe("running");
  });

  test("a response without a gate (proxy down) clears it", () => {
    const store = new VizStore((flush) => flush());
    store.setState(withGate(gate("paused", 100)));
    const { gate: _, ...down } = withGate(gate("paused", 101));
    store.setState(down);
    expect(store.getSnapshot().state?.gate).toBeUndefined();
  });
});

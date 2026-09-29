// Shared test fixture: GET /api/state as served by viz_feed.ReplayDriver over
// logs/session_20260929_163420, stepped to just past the bank errand
// (keepalive/animation events trimmed, account name redacted).
import raw from "./state_163420.json";
import type { StateResponse } from "../types.ts";

export const fixture = raw as unknown as StateResponse;
export const fixtureEvents = fixture.events ?? [];

/** Deep copy so a test can mutate the snapshot freely. */
export function cloneFixture(): StateResponse {
  return structuredClone(fixture);
}

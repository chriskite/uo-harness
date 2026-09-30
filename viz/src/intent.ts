// Agent intent (StateResponse.intent) -> what the Agent panel and the map show.
import { fmtDuration } from "./format.ts";
import type { AgentIntent, EventEnvelope, StateResponse } from "./types.ts";

export type IntentTone = "ok" | "warn" | "bad" | "info" | "dim";

/** Badge tone per phase kind: waiting on the human stands out, stops are red. */
const TONE: Record<string, IntentTone> = {
  captcha: "warn",
  lockout: "dim",
  stopped: "bad",
  done: "ok",
  trip_done: "ok",
};

export interface IntentView {
  text: string;
  kind: string;
  tone: IntentTone;
  /** "lumber · trip 1/3" (whatever of loop/trip is known), or null. */
  context: string | null;
  /** "for 0:42" since it was reported, or null when the clock is unknown. */
  age: string | null;
  target: string | null;
}

/** The clock to age an intent against: the live wall clock, or in replay the
 * newest event's time (the replay's own "now"). */
export function intentClock(state: StateResponse | null, events: readonly EventEnvelope[], wallNow: number): number | null {
  if (state?.viz?.mode === "replay") return events.length ? events[events.length - 1]!.t : null;
  return wallNow;
}

export function intentView(intent: AgentIntent | null | undefined, now: number | null): IntentView | null {
  if (!intent) return null;
  const kind = intent.kind ?? "doing";
  const ctx: string[] = [];
  if (intent.loop) ctx.push(intent.loop);
  if (intent.trip !== undefined) ctx.push(intent.trips !== undefined ? `trip ${intent.trip}/${intent.trips}` : `trip ${intent.trip}`);
  return {
    text: intent.text,
    kind,
    tone: TONE[kind] ?? "info",
    context: ctx.length ? ctx.join(" · ") : null,
    age: now === null ? null : `for ${fmtDuration(now - intent.since)}`,
    target: intent.target ? `${intent.target[0]},${intent.target[1]}` : null,
  };
}

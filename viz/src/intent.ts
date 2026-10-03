// Agent intent (StateResponse.intent / intents) -> what the Avatar panel and the map show.
import { fmtDuration } from "./format.ts";
import type { AgentIntent, EventEnvelope, Snapshot, StateResponse, Tile } from "./types.ts";

export type IntentTone = "ok" | "warn" | "bad" | "info" | "dim";
/** spin: working on it; wait: blocked on a captcha solve or a timer; idle: finished or stopped. */
export type IntentActivity = "spin" | "wait" | "idle";

/** Badge tone per phase kind: a captcha wait stands out, fights and stops are red. */
const TONE: Record<string, IntentTone> = {
  captcha: "warn",
  speech: "warn",
  lockout: "dim",
  stopped: "bad",
  attack: "bad",
  done: "ok",
  trip_done: "ok",
  arrived: "ok",
  idle: "dim",
};

const ACTIVITY: Record<string, IntentActivity> = {
  captcha: "wait",
  speech: "wait",
  lockout: "wait",
  stopped: "idle",
  done: "idle",
  trip_done: "idle",
  arrived: "idle",
  idle: "idle",
};

/** Where the map marker goes: the followed entity's current tile (a mob moves
 * while being fought) when the world knows it, else the intent's fixed tile. */
export function intentGoal(intent: AgentIntent | null | undefined, world: Snapshot | undefined): Tile | null {
  if (!intent) return null;
  const s = intent.target_serial;
  if (s && world) {
    const e = world.mobiles?.[s] ?? world.items?.[s];
    if (e && e.x !== undefined && e.y !== undefined) return [e.x, e.y];
  }
  return intent.target ?? null;
}

/** Health fraction 0..1 of an entity, or null when unknown (hits_max 0 or missing). */
export function hpFraction(hits: number | undefined, hitsMax: number | undefined): number | null {
  if (hits === undefined || !hitsMax) return null;
  return Math.max(0, Math.min(1, hits / hitsMax));
}

/** Healthbar colour like the game's: green, then yellow below 50%, red below 25%. */
export function hpColor(f: number): string {
  return f < 0.25 ? "#ef4444" : f < 0.5 ? "#eab308" : "#22c55e";
}

export interface IntentView {
  text: string;
  kind: string;
  tone: IntentTone;
  activity: IntentActivity;
  /** "lumber · trip 1/3" (whatever of loop/trip is known), or null. */
  context: string | null;
  /** "for 0:42" since this step started, or null when the clock is unknown. */
  age: string | null;
  target: string | null;
}

export interface RecentIntent {
  key: string;
  text: string;
  kind: string;
  tone: IntentTone;
  /** "6s ago": when the step ended; null when the clock is unknown. */
  ago: string | null;
  /** How long the step lasted, "0:42". */
  took: string;
}

/** The clock to age an intent against: the live wall clock, or in replay the
 * newest event's time (the replay's own "now"). */
export function intentClock(state: StateResponse | null, events: readonly EventEnvelope[], wallNow: number): number | null {
  if (state?.viz?.mode === "replay") return events.length ? events[events.length - 1]!.t : null;
  return wallNow;
}

/** Seconds -> "6s ago", "4m ago", "2h 5m ago" (negatives clamp to 0). */
export function fmtAgo(s: number): string {
  const t = Math.max(0, Math.floor(s));
  if (t < 60) return `${t}s ago`;
  if (t < 3600) return `${Math.floor(t / 60)}m ago`;
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  return m ? `${h}h ${m}m ago` : `${h}h ago`;
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
    activity: ACTIVITY[kind] ?? "spin",
    context: ctx.length ? ctx.join(" · ") : null,
    age: now === null ? null : `for ${fmtDuration(now - intent.since)}`,
    target: intent.target ? `${intent.target[0]},${intent.target[1]}` : null,
  };
}

/** Finished steps (those with `until`), newest first, at most `limit`. */
export function recentIntents(history: readonly AgentIntent[] | undefined, now: number | null, limit = 10): RecentIntent[] {
  const out: RecentIntent[] = [];
  for (let i = (history?.length ?? 0) - 1; i >= 0 && out.length < limit; i--) {
    const h = history![i]!;
    if (h.until === undefined) continue;
    const kind = h.kind ?? "doing";
    out.push({
      key: `${h.since}:${i}`,
      text: h.text,
      kind,
      tone: TONE[kind] ?? "info",
      ago: now === null ? null : fmtAgo(now - h.until),
      took: fmtDuration(h.until - h.since),
    });
  }
  return out;
}

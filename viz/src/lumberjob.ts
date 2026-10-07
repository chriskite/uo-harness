// The left-column Lumber job pane's model: whether a lumber run is going, where it is,
// and the per-wood tallies of this trip and of past trips at its spot.
import { type JobTrip } from "./jobs.ts";
import type { AgentIntent } from "./types.ts";

/** Intent kinds that end a lumber run (loop_lumber.py: done, break_due, stop_intent's stopped). trip_done is between trips. */
export const LUMBER_ENDED: ReadonlySet<string> = new Set(["done", "break_due", "stopped"]);

/** The current intent when it belongs to a running lumber run, else null. */
export function runningLumber(intent: AgentIntent | null | undefined): AgentIntent | null {
  return intent?.loop === "lumber" && !LUMBER_ENDED.has(intent.kind ?? "") ? intent : null;
}

export type LumberLeg = "out" | "grove" | "home";

const LEG: Record<string, LumberLeg> = {
  leave_room: "out",
  resupply: "out",
  mount: "out",
  aspect: "out",
  to_library: "out",
  recall_out: "out",
  to_tree: "grove",
  chop: "grove",
  lockout: "grove",
  escape: "grove",
  flee: "grove",
  recall_home: "home",
  to_room: "home",
  convert: "home",
  store: "home",
  trip_done: "home",
};

/** Where the run is. Kinds that can happen anywhere (track, speech, captcha) take the newest
 *  earlier history entry with a known leg; "out" when none has one. */
export function lumberLeg(intent: AgentIntent, history: readonly AgentIntent[] | undefined): LumberLeg {
  const own = LEG[intent.kind ?? ""];
  if (own) return own;
  for (let i = (history?.length ?? 0) - 1; i >= 0; i--) {
    const leg = LEG[history?.[i]?.kind ?? ""];
    if (leg) return leg;
  }
  return "out";
}

/** The chop rate's trailing window, and the least grove time it needs before it shows. */
export const RATE_WINDOW_S = 300;
export const RATE_MIN_S = 60;

export interface LogRate {
  /** logs gained per hour over the span */
  logsH: number;
  /** seconds the rate is measured over (up to RATE_WINDOW_S) */
  spanS: number;
}

const total = (woods: Record<string, number> | undefined) => Object.values(woods ?? {}).reduce((s, n) => s + n, 0);

/** Logs/hr right now: the logs gained since the tally of `windowS` ago (or since the field run began,
 *  when that is later), over the time since then, ending at `now`. Samples are the closed history
 *  entries of this trip: the tally at an entry's `until` is its last update. A field run begins at
 *  the end of the last step that isn't at the grove or is the travel lockout; a tally that drops
 *  (a theft) starts over. Null away from the grove, without a tally or under RATE_MIN_S of samples. */
export function currentRate(
  intent: AgentIntent,
  history: readonly AgentIntent[] | undefined,
  now: number | null,
  windowS = RATE_WINDOW_S,
): LogRate | null {
  if (now === null || intent.woods === undefined || intent.kind === "lockout") return null;
  if (lumberLeg(intent, history) !== "grove") return null;
  let samples: [number, number][] = [];
  for (const h of history ?? []) {
    if (h.until === undefined) continue; // the current step
    const leg = LEG[h.kind ?? ""];
    if (h.trip !== intent.trip || (leg !== undefined && leg !== "grove") || h.kind === "lockout") samples = [];
    if (h.trip !== intent.trip || h.woods === undefined) continue;
    const n = total(h.woods);
    if (samples.length && n < samples[samples.length - 1]![1]) samples = [];
    samples.push([h.until, n]);
  }
  const cur = total(intent.woods);
  const last = samples[samples.length - 1];
  if (last && cur < last[1]) samples = [];
  let start = samples[0];
  for (const s of samples) if (s[0] <= now - windowS) start = s;
  if (!start || now - start[0] < RATE_MIN_S) return null;
  const spanS = now - start[0];
  return { logsH: ((cur - start[1]) / spanS) * 3600, spanS };
}

/** Trips at `spot`, newest first (by t_start, null last). */
export function spotTrips(trips: readonly JobTrip[], spot: string): JobTrip[] {
  return trips
    .filter((t) => t.spot === spot)
    .sort((a, b) => (b.t_start ?? -Infinity) - (a.t_start ?? -Infinity));
}

/** Logs by wood summed over trips' `woods`. */
export function sumWoods(trips: readonly JobTrip[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const t of trips) for (const [w, n] of Object.entries(t.woods ?? {})) out[w] = (out[w] ?? 0) + n;
  return out;
}

export interface WoodMix {
  name: string;
  logs: number;
  share: number;
}

/** Woods with logs > 0, most logs first (ties by name), with their share of the total. */
export function woodMix(woods: Record<string, number> | undefined): WoodMix[] {
  const rows = Object.entries(woods ?? {}).filter(([, n]) => n > 0);
  const total = rows.reduce((s, [, n]) => s + n, 0);
  return rows
    .map(([name, logs]) => ({ name, logs, share: logs / total }))
    .sort((a, b) => b.logs - a.logs || a.name.localeCompare(b.name));
}

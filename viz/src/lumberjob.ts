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

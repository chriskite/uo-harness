// Damage over a sim trace in time bins, split by source (the Sim page's stacked-bar charts).
import type { Trace } from "./trace.ts";

export interface DamageBin {
  /** Bin start (sim s). */
  t: number;
  by: Record<string, number>;
}

export const OTHER = "other";
const TOP = 6;

/** `hurt` totals per `binS` bins over [t0, t1]: on `victim`, or (`team`) by the player and pets on monsters.
 *  A source is labelled by its key or name (`kind`, e.g. "bleed", when there is no attacker); the top 6
 *  sources by total keep their label, the rest add up under "other". */
export function damageBins(trace: Trace, f: { victim?: number; team?: true }, binS = 5): DamageBin[] {
  const n = Math.max(1, Math.ceil((trace.t1 - trace.t0) / binS));
  const raw: { i: number; label: string; n: number }[] = [];
  const totals = new Map<string, number>();
  for (const e of trace.events) {
    if (e.e !== "hurt" || e.v === null || e.v === undefined) continue;
    if (f.victim !== undefined && e.v !== f.victim) continue;
    const by = e.by !== null && e.by !== undefined ? trace.names.get(e.by) : undefined;
    const team = by !== undefined && (by.kind === "player" || by.kind === "pet");
    if (f.team && !(team && trace.names.get(e.v)?.kind === "monster")) continue;
    const label = e.by === null || e.by === undefined ? (e.kind ?? "?") : by?.key || by?.name || String(e.by);
    const i = Math.min(n - 1, Math.max(0, Math.floor((e.t - trace.t0) / binS)));
    const amount = e.n ?? 0;
    raw.push({ i, label, n: amount });
    totals.set(label, (totals.get(label) ?? 0) + amount);
  }
  const top = new Set([...totals.entries()].sort((a, b) => b[1] - a[1]).slice(0, TOP).map(([l]) => l));
  const bins: DamageBin[] = Array.from({ length: n }, (_, i) => ({ t: trace.t0 + i * binS, by: {} }));
  for (const r of raw) {
    const label = top.has(r.label) ? r.label : OTHER;
    const by = bins[r.i]!.by;
    by[label] = (by[label] ?? 0) + r.n;
  }
  return bins;
}

/** The labels in `bins`, biggest total first, "other" last. */
export function binLabels(bins: readonly DamageBin[]): string[] {
  const totals = new Map<string, number>();
  for (const b of bins) for (const [l, n] of Object.entries(b.by)) totals.set(l, (totals.get(l) ?? 0) + n);
  return [...totals.entries()]
    .sort((a, b) => (a[0] === OTHER ? 1 : b[0] === OTHER ? -1 : b[1] - a[1]))
    .map(([l]) => l);
}

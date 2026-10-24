// viz_server's sim routes (harness/viz_server.py): the trace list, one trace file (sent gzip-encoded;
// the browser inflates it) and line of sight on the sim's map. They never touch the live/replay feed.
import type { TraceListing } from "./types.ts";

export interface TraceList {
  dir: string;
  traces: TraceListing[];
}

export async function listTraces(): Promise<TraceList> {
  const r = await fetch("/api/sim/traces", { cache: "no-store" });
  if (!r.ok) throw new Error(`/api/sim/traces: HTTP ${r.status} ${await r.text()}`);
  return (await r.json()) as TraceList;
}

export async function fetchTrace(name: string): Promise<string> {
  const r = await fetch(`/api/sim/trace/${encodeURIComponent(name)}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`/api/sim/trace/${name}: HTTP ${r.status} ${await r.text()}`);
  return await r.text();
}

export type Point3 = readonly [number, number, number];

interface LosResponse {
  see: boolean[];
}

/** Whether `from` sees each of `tos` (at most 64 per call). */
export async function los(from: Point3, tos: readonly Point3[]): Promise<boolean[]> {
  const q = `from=${from.join(",")}&to=${tos.map((p) => p.join(",")).join(";")}`;
  const r = await fetch(`/api/sim/los?${q}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`/api/sim/los: HTTP ${r.status} ${await r.text()}`);
  const body = (await r.json()) as LosResponse; // the route's documented reply
  return body.see;
}

/** URL of one 64×64-tile map chunk (floor grey, walls brown, nothing transparent). */
export function mapChunkUrl(cx: number, cy: number): string {
  return `/api/sim/map/${cx}/${cy}.png`;
}

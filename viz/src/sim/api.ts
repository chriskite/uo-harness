// viz_server's sim routes (harness/viz_server.py): the trace list, one trace file (sent gzip-encoded;
// the browser inflates it), line of sight on the sim's map, and the sim jobs (run.py / fight*.py started
// from the Sim page, one at a time). They never touch the live/replay feed.
import type { SimJob, SimJobRequest, SimOptions, TraceListing } from "./types.ts";

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

/** The server's `{"error"}` text of a failed reply, else `HTTP <status>`. */
async function errorOf(r: Response): Promise<string> {
  const text = await r.text();
  try {
    const body: unknown = JSON.parse(text);
    if (body && typeof body === "object" && "error" in body) return String(body.error);
  } catch {
    // not JSON: fall through with the raw text
  }
  return `HTTP ${r.status}${text ? ` ${text}` : ""}`;
}

/** What a job can be started with: scenarios, run.py defaults and limits, the fight scripts. */
export async function fetchSimOptions(): Promise<SimOptions> {
  const r = await fetch("/api/sim/options", { cache: "no-store" });
  if (!r.ok) throw new Error(`/api/sim/options: ${await errorOf(r)}`);
  return (await r.json()) as SimOptions;
}

interface JobsResponse {
  jobs: SimJob[];
}

interface StartResponse {
  ok: true;
  job: SimJob;
}

/** The recent jobs, newest first (at most 20). */
export async function listSimJobs(): Promise<SimJob[]> {
  const r = await fetch("/api/sim/jobs", { cache: "no-store" });
  if (!r.ok) throw new Error(`/api/sim/jobs: ${await errorOf(r)}`);
  const body = (await r.json()) as JobsResponse; // the route's documented reply
  return body.jobs;
}

/** Start a job; throws with the server's error (400 a bad option, 409 a job already running). */
export async function startSimJob(req: SimJobRequest): Promise<SimJob> {
  const r = await fetch("/api/sim/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) throw new Error(await errorOf(r));
  const body = (await r.json()) as StartResponse; // the route's documented reply
  return body.job;
}

/** Stop a running job; throws with the server's error (404 unknown, 409 not running). */
export async function cancelSimJob(id: number): Promise<void> {
  const r = await fetch("/api/sim/jobs/cancel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  if (!r.ok) throw new Error(await errorOf(r));
}

// The mining ore map layer's model (docs/VISUALIZER.md §2.21): the /api/mining/ore answer, and
// what an ore block, a stand and a dug stand under the cursor say. The spot to ask for is
// grove.ts groveQuery's choice (the intent's spot, else the spot around us).
import type { Tile } from "./types.ts";

export type BlockState = "ready" | "depleted" | "no_harvest";

/** One 8x8 ore block a stand of the spot reaches (harness/mining_opt.py ore_view). */
export interface OreBlock {
  bx: number;
  by: number;
  state: BlockState;
  /** depleted: when a stand said nothing nearby and when the regrowth window ends (epoch s) */
  since?: number;
  until?: number;
}

/** A stand tile the runner dug at within the window (mine_attempts). */
export interface DugStand {
  x: number;
  y: number;
  /** digs that gave ore or failed (the stand-ending answers aside) */
  digs: number;
  ore: number;
  last_t: number;
  /** the answer that ended the stand: nothing in reach has ore / no mining allowed; null: still open or left */
  end: "nothing_near" | "no_harvest" | null;
}

export interface OreCounts {
  blocks: number;
  ready: number;
  depleted: number;
  no_harvest: number;
  stands: number;
  /** stands reaching at least one ready block */
  stands_ready: number;
  dug_stands: number;
  dug_ore: number;
}

export interface OreSpot {
  id: string;
  name: string | null;
  facet: number | null;
  status: string | null;
  pvp: boolean | null;
  forge: { x: number; y: number; z: number } | null;
  /** the area's square; an ore field (harness/mining_fields.py) also its `cells`, squares of `cell` tiles */
  area: { center: Tile; radius: number; cells?: [number, number][]; cell?: number };
}

export interface OreResponse {
  spot: OreSpot | null;
  store: boolean;
  error?: string;
  regrow_min?: number;
  /** a block's side in tiles (8) */
  block?: number;
  now?: number;
  counts?: OreCounts;
  blocks?: OreBlock[];
  /** [x, y, blocks reached, of those ready] */
  stands?: [number, number, number, number][];
  dug?: DugStand[];
}

export async function fetchOre(query: string): Promise<OreResponse> {
  const r = await fetch(`/api/mining/ore?${query}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`/api/mining/ore: HTTP ${r.status} ${await r.text()}`);
  return (await r.json()) as OreResponse;
}

/** The ore block holding tile (x, y), if the spot's stands reach it. */
export function blockAt(o: OreResponse | null, x: number, y: number): OreBlock | null {
  const b = o?.block ?? 8;
  const bx = Math.floor(x / b);
  const by = Math.floor(y / b);
  return o?.blocks?.find((k) => k.bx === bx && k.by === by) ?? null;
}

const mins = (s: number) => `${Math.max(0, Math.round(s / 60))}m`;

/** HUD lines for tile (x, y): its ore block, the stand on it, what was dug there; ages against `now` (epoch s). */
export function oreText(o: OreResponse, x: number, y: number, now: number): string[] {
  const out: string[] = [];
  const k = blockAt(o, x, y);
  if (k) {
    const b = o.block ?? 8;
    const head = `ore block ${k.bx},${k.by} (${k.bx * b}-${k.bx * b + b - 1}, ${k.by * b}-${k.by * b + b - 1})`;
    if (k.state === "depleted") out.push(`${head}: dug out ${mins(now - (k.since ?? now))} ago, ready in ${mins((k.until ?? now) - now)}`);
    else if (k.state === "no_harvest") out.push(`${head}: the server says no mining here`);
    else out.push(`${head}: ore as far as we know`);
  }
  const s = o.stands?.find(([sx, sy]) => sx === x && sy === y);
  if (s) out.push(`stand ${x},${y}: reaches ${s[2]} block${s[2] === 1 ? "" : "s"}, ${s[3]} with ore`);
  const d = o.dug?.find((g) => g.x === x && g.y === y);
  if (d) {
    const end = d.end === "nothing_near" ? ", then nothing in reach had ore" : d.end === "no_harvest" ? ", then no mining allowed" : "";
    out.push(`dug ${mins(now - d.last_t)} ago: ${d.digs} dig${d.digs === 1 ? "" : "s"}, ${d.ore} ore${end}`);
  }
  return out;
}

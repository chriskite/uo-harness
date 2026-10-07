// The lumber grove map layer's model (docs/VISUALIZER.md §2.12): which spot to ask
// /api/lumber/grove for, and what a tree under the cursor says.
import type { AgentIntent, Tile } from "./types.ts";

export type TreeState = "ready" | "depleted" | "unreachable" | "not_tree";

/** A tree-named static at or around the spot (harness/lumber_opt.py grove_view). */
export interface GroveTree {
  x: number;
  y: number;
  z: number;
  graphic: string;
  /** tiledata name, e.g. "cedar tree" */
  name: string | null;
  /** a seed tree of the spot (lumber_spots), not found on the map */
  seed: boolean;
  /** "tree": one the runner tries when inside the area; "excluded": find_trees leaves it out (`why`) */
  kind: "tree" | "excluded";
  why?: "passable" | "unchoppable" | "potted" | "stump";
  /** within the spot's area square (the runner's candidates) */
  inside: boolean;
  /** trees only: harvest memory over the plan's regrowth window */
  state?: TreeState;
  /** depleted / unreachable: when it was marked and when the window ends (epoch s) */
  since?: number;
  until?: number;
}

export interface GroveCounts {
  trees: number;
  ready: number;
  depleted: number;
  unreachable: number;
  not_tree: number;
  excluded: number;
  outside: number;
}

export interface GroveSpot {
  id: string;
  name: string | null;
  facet: number | null;
  status: string | null;
  pvp: boolean | null;
  area: { center: Tile; radius: number };
}

export interface GroveResponse {
  spot: GroveSpot | null;
  store: boolean;
  error?: string;
  regrow_min?: number;
  regrow_fitted?: boolean;
  margin?: number;
  now?: number;
  counts?: GroveCounts;
  trees?: GroveTree[];
}

/** The /api/lumber/grove query for a running lumber intent: its spot, else the spot
 *  around the true position (runners older than the intent's `spot`). Null: nothing to ask. */
export function groveQuery(intent: AgentIntent | null, pos: Tile | null, facet: number | null | undefined): string | null {
  if (!intent) return null;
  if (intent.spot) return `spot=${encodeURIComponent(intent.spot)}`;
  if (!pos) return null;
  return `facet=${facet ?? 0}&x=${pos[0]}&y=${pos[1]}`;
}

export async function fetchGrove(query: string): Promise<GroveResponse> {
  const r = await fetch(`/api/lumber/grove?${query}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`/api/lumber/grove: HTTP ${r.status} ${await r.text()}`);
  return (await r.json()) as GroveResponse;
}

/** The grove's tree on tile (x, y), if any. */
export function treeAt(g: GroveResponse | null, x: number, y: number): GroveTree | null {
  return g?.trees?.find((t) => t.x === x && t.y === y) ?? null;
}

const WHY_TEXT: Record<string, string> = {
  passable: "not counted: passable art (not a trunk)",
  unchoppable: "not counted: the server refuses an axe on it",
  potted: "not counted: potted",
  stump: "not counted: stump",
};

const mins = (s: number) => `${Math.max(0, Math.round(s / 60))}m`;

/** One HUD line for a tree under the cursor, ages against `now` (epoch s). */
export function treeText(t: GroveTree, now: number): string {
  const head = `${t.name ?? "tree"} ${t.graphic}${t.seed ? " (seed)" : ""}`;
  if (t.kind === "excluded") return `${head}: ${WHY_TEXT[t.why ?? ""] ?? "not counted"}${t.inside ? "" : ", outside the area"}`;
  const where = t.inside ? "" : " · outside the area: the runner doesn't try it";
  switch (t.state) {
    case "depleted":
      return `${head}: depleted ${mins(now - (t.since ?? now))} ago, ready in ${mins((t.until ?? now) - now)}${where}`;
    case "unreachable":
      return `${head}: unreachable ${mins(now - (t.since ?? now))} ago, retried in ${mins((t.until ?? now) - now)}${where}`;
    case "not_tree":
      return `${head}: the server says it isn't a tree${where}`;
    default:
      return `${head}: choppable${where}`;
  }
}

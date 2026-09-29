// Walk-memory underlay and true-vs-dead-reckoned position helpers.
import type { LiveWalk } from "./events.ts";
import type { Movement, SelfState, Tile, WalkMemoryFile } from "./types.ts";

/** Facing deltas: 0=N 1=NE 2=E 3=SE 4=S 5=SW 6=W 7=NW (runtime.py _DELTAS). */
export const DIR_DELTAS: ReadonlyArray<Tile> = [
  [0, -1],
  [1, -1],
  [1, 0],
  [1, 1],
  [0, 1],
  [-1, 1],
  [-1, 0],
  [-1, -1],
];

export type Edge = [number, number, number, number];
export type Blocked = [number, number, number];

export interface WalkLayer {
  tiles: Tile[];
  /** Undirected, deduplicated. */
  edges: Edge[];
  blocked: Blocked[];
}

function asTile(v: unknown): Tile | null {
  return Array.isArray(v) && typeof v[0] === "number" && typeof v[1] === "number" ? [v[0], v[1]] : null;
}

/** Accepts the file's flat [x1,y1,x2,y2] and the nested [[x,y],[x,y]] form. */
function asEdge(v: unknown): Edge | null {
  if (!Array.isArray(v)) return null;
  if (v.length === 4 && v.every((n): n is number => typeof n === "number")) return [...v] as Edge;
  const a = asTile(v[0]);
  const b = asTile(v[1]);
  return a && b ? [a[0], a[1], b[0], b[1]] : null;
}

/** Accepts the file's flat [x,y,dir] and the nested [[x,y],dir] form. */
function asBlocked(v: unknown): Blocked | null {
  if (!Array.isArray(v)) return null;
  if (v.length === 3 && v.every((n): n is number => typeof n === "number")) return [...v] as Blocked;
  const a = asTile(v[0]);
  return a && typeof v[1] === "number" ? [a[0], a[1], v[1]] : null;
}

/** Merge walkmem.json with the live proxy `step`/`blocked` events into one deduplicated layer. */
export function buildWalkLayer(file: WalkMemoryFile | null, live: LiveWalk): WalkLayer {
  const tiles = new Map<string, Tile>();
  const edges = new Map<string, Edge>();
  const blocked = new Map<string, Blocked>();
  const addTile = (t: Tile) => tiles.set(`${t[0]},${t[1]}`, t);
  const addEdge = (e: Edge) => {
    const [x1, y1, x2, y2] = e;
    const fwd = x1 < x2 || (x1 === x2 && y1 <= y2);
    const n: Edge = fwd ? e : [x2, y2, x1, y1];
    edges.set(n.join(","), n);
    addTile([x1, y1]);
    addTile([x2, y2]);
  };
  const addBlocked = (b: Blocked) => blocked.set(b.join(","), b);

  if (file) {
    for (const v of file.tiles ?? []) {
      const t = asTile(v);
      if (t) addTile(t);
    }
    for (const v of file.edges ?? []) {
      const e = asEdge(v);
      if (e) addEdge(e);
    }
    for (const v of file.blocked ?? []) {
      const b = asBlocked(v);
      if (b) addBlocked(b);
    }
  }
  for (const [a, b] of live.steps) addEdge([a[0], a[1], b[0], b[1]]);
  for (const [a, d] of live.blocked) addBlocked([a[0], a[1], d]);
  return { tiles: [...tiles.values()], edges: [...edges.values()], blocked: [...blocked.values()] };
}

export interface Divergence {
  diverged: boolean;
  /** Dead-reckoned minus true, in tiles (0 when there is no truth). */
  dx: number;
  dy: number;
}

/** The world model's position (`world.self`) disagrees with the server-true
 * `movement.pos` on x or y. No truth yet = nothing to disagree with. */
export function divergence(movement: Movement | null | undefined, self: SelfState | null | undefined): Divergence {
  const pos = movement?.pos;
  if (!pos || !self) return { diverged: false, dx: 0, dy: 0 };
  const dx = self.x - pos[0];
  const dy = self.y - pos[1];
  return { diverged: dx !== 0 || dy !== 0, dx, dy };
}

/** Position to centre on / draw as self: truth, else the world model's. */
export function truePosition(movement: Movement | null | undefined, self: SelfState | null | undefined): Tile | null {
  if (movement?.pos) return [movement.pos[0], movement.pos[1]];
  if (self && self.position_absolute) return [self.x, self.y];
  return null;
}

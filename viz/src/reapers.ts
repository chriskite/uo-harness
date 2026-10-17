// The Field of Souls reaper heatmap's model (docs/VISUALIZER.md §2.17): /api/fos/reapers (harness/fos_field.py
// reaper_map), which field instance a tile is in, per-cell rates per hour watched, colours and the hover line.

/** Spawns: reapers that appeared inside our view; deaths: reapers that died in our view. */
export type ReaperMode = "spawns" | "deaths";

/** [ix, iy, watched_view_s, watched_spawn_s, deaths, spawns]: one CELL x CELL square of the field frame. */
export type ReaperCellRow = [number, number, number, number, number, number];

export interface FieldInstance {
  name: string;
  /** Added to a field-frame tile to get the instance's tile. */
  offset: [number, number];
}

export interface ReaperMap {
  name?: string;
  /** The island in the field frame: x0, y0, x1, y1 (exclusive). */
  box?: [number, number, number, number];
  cell?: number;
  view_r?: number;
  spawn_r?: number;
  instances?: FieldInstance[];
  runs: number;
  watched_s?: number;
  cells: ReaperCellRow[];
  spawns: [number, number][];
  store?: boolean;
}

/** A cell watched for less than this has no rate (one reaper in a few seconds is noise). */
export const MIN_WATCH_S = 60;
/** The map button shows within this many tiles of an instance's island. */
export const NEAR_FIELD = 40;
export const REAPER_POLL_MS = 60_000;
/** Cells each side pooled into a cell's rate (4-tile cells: a 12 x 12-tile area). One run gives a cell a
 *  spawn or two; pooling turns those single squares into areas. */
export const POOL = 1;

export interface HeatCell {
  /** Field-frame tile of the cell's corner. */
  x: number;
  y: number;
  size: number;
  /** Per hour watched over the pooled area; null when it was watched under MIN_WATCH_S. */
  rate: number | null;
  /** Spawns or deaths in the pooled area. */
  count: number;
  /** The pooled area's mean seconds watched per cell. */
  watched: number;
  /** rate against the scale's top (95th percentile of the positive rates), 0..1. */
  level: number;
}

/** The instance whose island (grown by `margin` tiles) holds the absolute tile (x, y), else null. */
export function instanceAt(map: ReaperMap | null, x: number, y: number, margin = 0): FieldInstance | null {
  if (!map?.box || !map.instances) return null;
  const [x0, y0, x1, y1] = map.box;
  for (const inst of map.instances) {
    const fx = x - inst.offset[0];
    const fy = y - inst.offset[1];
    if (fx >= x0 - margin && fx < x1 + margin && fy >= y0 - margin && fy < y1 + margin) return inst;
  }
  return null;
}

function rowRate(row: ReaperCellRow, mode: ReaperMode): { count: number; watched: number } {
  return mode === "spawns" ? { count: row[5], watched: row[3] } : { count: row[4], watched: row[2] };
}

function indexRows(map: ReaperMap): Map<string, ReaperCellRow> {
  return new Map(map.cells.map((r) => [`${r[0]},${r[1]}`, r]));
}

/** The area around cell (ix, iy), POOL cells each side: its count and its mean seconds watched per cell. */
function pooled(rows: Map<string, ReaperCellRow>, ix: number, iy: number, mode: ReaperMode): { count: number; watched: number } {
  let count = 0;
  let watched = 0;
  let n = 0;
  for (let dx = -POOL; dx <= POOL; dx++) {
    for (let dy = -POOL; dy <= POOL; dy++) {
      const row = rows.get(`${ix + dx},${iy + dy}`);
      if (!row) continue;
      const r = rowRate(row, mode);
      count += r.count;
      watched += r.watched;
      n++;
    }
  }
  return { count, watched: n ? watched / n : 0 };
}

const rateOf = (count: number, watched: number) => (watched >= MIN_WATCH_S ? (count * 3600) / watched : null);

/** The scale's top: the 95th percentile of the positive rates (1 without any). */
export function scaleTop(rates: number[]): number {
  const pos = rates.filter((r) => r > 0).sort((a, b) => a - b);
  if (!pos.length) return 1;
  return pos[Math.min(pos.length - 1, Math.floor(0.95 * (pos.length - 1)))]!;
}

/** The cells of `mode` with their pooled rate per hour watched and level, and the scale's top. */
export function heatCells(map: ReaperMap | null, mode: ReaperMode): { cells: HeatCell[]; top: number } {
  if (!map?.box || !map.cell) return { cells: [], top: 1 };
  const [x0, y0] = map.box;
  const size = map.cell;
  const rows = indexRows(map);
  const raw = map.cells.map((row) => {
    const { count, watched } = pooled(rows, row[0], row[1], mode);
    return { x: x0 + row[0] * size, y: y0 + row[1] * size, size, count, watched, rate: rateOf(count, watched) };
  });
  const top = scaleTop(raw.flatMap((c) => (c.rate !== null ? [c.rate] : [])));
  return { cells: raw.map((c) => ({ ...c, level: c.rate !== null ? Math.min(1, c.rate / top) : 0 })), top };
}

/** Fill colour of a heat cell: dark red to yellow by level; a faint grey when watched long enough with none;
 *  null (not drawn) when watched too little. */
export function heatColor(c: HeatCell): string | null {
  if (c.rate === null) return null;
  if (c.rate === 0) return "rgba(148, 163, 184, 0.10)";
  const l = c.level;
  const g = Math.round(40 + 190 * l);
  return `rgba(${Math.round(185 + 70 * l)}, ${g}, ${Math.round(30 * l)}, ${(0.25 + 0.5 * l).toFixed(2)})`;
}

const fmtMin = (s: number) => `${(s / 60).toFixed(s < 600 ? 1 : 0)} min`;

/** The hover line for absolute tile (x, y): the pooled area's spawns and deaths over the time it was watched
 *  (what the colour shows). */
export function cellText(map: ReaperMap | null, x: number, y: number): string | null {
  const inst = instanceAt(map, x, y);
  if (!map?.box || !map.cell || !inst) return null;
  const fx = x - inst.offset[0];
  const fy = y - inst.offset[1];
  const ix = Math.floor((fx - map.box[0]) / map.cell);
  const iy = Math.floor((fy - map.box[1]) / map.cell);
  const span = (2 * POOL + 1) * map.cell;
  const where = `reapers around ${fx},${fy}${inst.offset[0] || inst.offset[1] ? ` (${inst.name} ${x},${y})` : ""}, ${span}x${span} tiles`;
  const rows = indexRows(map);
  const s = pooled(rows, ix, iy, "spawns");
  const d = pooled(rows, ix, iy, "deaths");
  if (!s.watched && !d.watched) return `${where}: never watched`;
  const part = (p: { count: number; watched: number }, one: string, many: string) => {
    const rate = rateOf(p.count, p.watched);
    return `${p.count} ${p.count === 1 ? one : many} in ${fmtMin(p.watched)}${rate !== null ? ` (${rate.toFixed(0)}/h)` : ""}`;
  };
  return `${where}: ${part(s, "spawn", "spawns")} · ${part(d, "death", "deaths")}`;
}

export async function fetchReapers(): Promise<ReaperMap> {
  const r = await fetch("/api/fos/reapers", { cache: "no-store" });
  if (!r.ok) throw new Error(`/api/fos/reapers: HTTP ${r.status} ${await r.text()}`);
  return (await r.json()) as ReaperMap;
}

// The simulator trace file (harness/sim/trace.py, docs/SIM.md): one JSON object per line, gzip on disk.
// head, then keyframes (`key`, full state) every KEY_EVERY_S, per-tick deltas (`d`), events (`e`) and `end`.
// Times are the sim clock (world.t) rounded to 0.01; serials are ints, except as JSON object keys
// (`d.m`, `th`, `db`), where they are decimal strings.

export interface RangedSpec {
  range: number;
  keep: number;
  every_s: number;
  /** [q10, q90, q50] seconds. */
  cast_s?: number[] | null;
  flight_s?: number[] | null;
}

export interface SpecialSpec {
  kind: string;
  range?: number | null;
  radius?: number | null;
  far_r?: number | null;
}

/** head.specs[key]: what the viewer needs of a creature type (range rings, swing timing). */
export interface CreatureSpec {
  aggro: number | null;
  melee: boolean;
  swing_s: number | number[] | null;
  ranged: RangedSpec | null;
  specials: SpecialSpec[];
}

export interface TraceHead {
  k: "head";
  v: number;
  label: string;
  seed: number;
  scenario: string;
  created: string;
  /** Wall-clock epoch s of sim t = 0 (the game clock). */
  epoch: number;
  tick: number;
  player: number;
  runner_args: string[];
  area: number[] | null;
  specs: Record<string, CreatureSpec>;
}

/** A mobile at one instant. Times are absolute sim seconds; 0 or null = inactive. */
export interface MobRec {
  s: number;
  kind: string;
  key: string | null;
  name: string;
  body: number;
  noto: number;
  x: number;
  y: number;
  z: number;
  d: number;
  h: number;
  hm: number;
  al: boolean;
  gh: boolean;
  w: boolean;
  tg: number | null;
  md: string | null;
  kl?: unknown;
  cp?: number | null;
  st?: number;
  sm?: number;
  mn?: number;
  mm?: number;
  /** Top 6 threat, serial (decimal string) -> amount. */
  th: Record<string, number>;
  db: Record<string, number>;
  ns: number;
  cd: Record<string, number>;
  /** [release time, target, start time]. */
  cs: [number, number, number] | null;
  po: number;
  bl: number;
  /** Diseases: [last tick time, damage per tick]. */
  ds: [number, number][];
  dc: number;
  ca: number;
  pv: [number, number] | null;
  hd: number;
  hi: number;
  im: number;
  ex: number | null;
  ch: [number, number] | null;
  fd: [number, number] | null;
}

export interface WorldRec {
  songs: Record<string, number>;
  /** Stationary-penalty steps left to walk (0 = none). */
  pen: number;
  par: number;
  ham: number;
  pot: number;
  rc: number | null;
  vet: number;
  pvp: number;
}

export interface ItemRec {
  s: number;
  kind?: string;
  g?: number;
  x: number;
  y: number;
  z?: number;
  name?: string;
  until?: number | null;
  t?: number;
  noto?: number;
}

export interface KeyLine {
  k: "key";
  t: number;
  mobs: MobRec[];
  w: WorldRec;
  items: ItemRec[];
}

export interface DeltaLine {
  k: "d";
  t: number;
  m?: Record<string, Partial<MobRec>>;
  add?: MobRec[];
  rm?: number[];
  w?: Partial<WorldRec>;
  ia?: ItemRec[];
  ir?: number[];
}

/** One event (plan's event table); the fields present depend on `e`. */
export interface TraceEvent {
  k: "e";
  t: number;
  e: string;
  v?: number | null;
  by?: number | null;
  m?: number | null;
  n?: number;
  kind?: string | null;
  h?: number;
  hit?: boolean;
  end?: number;
  action?: number;
  spell?: string | null;
  land?: number;
  g?: number | null;
  x?: number;
  y?: number;
  z?: number;
  why?: string;
  skill?: string;
  ok?: boolean | number;
  until?: number;
  phase?: string;
  dest?: unknown;
  key?: string | null;
  text?: string;
  sk?: unknown;
  target?: number | null;
}

export interface EndLine {
  k: "end";
  t: number;
  summary: Record<string, unknown>;
}

export interface Frame {
  t: number;
  mobs: Map<number, MobRec>;
  w: WorldRec;
  items: Map<number, ItemRec>;
}

export interface TraceListing {
  name: string;
  size: number;
  mtime: number;
  head: TraceHead | null;
}

// viz_server's sim job routes (/api/sim/options, /api/sim/jobs): run.py or a fight*.py started from the Sim page.

/** run.py's options as /api/sim/jobs takes them (`careers` 0 = run.py's default). */
export interface SimRunOptions {
  scenario: string;
  seed: number;
  runs: number;
  minutes: number;
  at_hunt: boolean;
  others: boolean;
  eerie: boolean;
  reds: boolean;
  prior: boolean;
  fresh: boolean;
  careers: number;
  trace: boolean;
  /** run.py --set lines, `module.NAME=value`. */
  sets: string[];
  /** Free text after `--` (split shell-style by the server). */
  runner_args: string;
}

/** Inclusive [min, max] of each numeric run option. */
export interface SimRunLimits {
  seed: [number, number];
  runs: [number, number];
  minutes: [number, number];
  careers: [number, number];
}

export type SimFightOption = { type: "flag"; arg: string } | { type: "choice"; args: string[] };

export interface SimFight {
  name: string;
  /** The script docstring's first line. */
  title: string;
  usage: string;
  options: SimFightOption[];
}

export interface SimOptions {
  scenarios: string[];
  /** The live store's hunt history for --prior. */
  prior: { available: boolean; path: string };
  run: { defaults: SimRunOptions; limits: SimRunLimits };
  fights: SimFight[];
}

export type SimJobRequest =
  | { kind: "run"; options: SimRunOptions }
  | { kind: "fight"; fight: string; seeds: string; args: string[]; trace: boolean };

export type SimJobStatus = "running" | "done" | "failed" | "cancelled";

export interface SimJob {
  id: number;
  kind: "run" | "fight";
  label: string;
  argv: string[];
  status: SimJobStatus;
  /** Unix seconds. */
  started: number;
  ended: number | null;
  exit: number | null;
  /** Trace file names the job wrote, newest first. */
  traces: string[];
  /** The last ≤200 output lines (stdout + stderr). */
  out: string[];
}

// Types for the state-port contract (docs/VISUALIZER.md §1, §1.4, §2.1).
// Snapshot serials are '0x%08X' strings; event serials are ints (serial.ts normalizes).

/** Snapshot-side serial key, always '0x%08X'. */
export type HexSerial = string;

export interface Movement {
  /** Server-true [x, y, z, facing] from the proxy's MoveAuthority. */
  pos: [number, number, number, number] | null;
  self_serial: number | null;
  inflight: number;
  next_seq: number;
  resync_pending: boolean;
  rejects_in_row: number;
  stalled: boolean;
  client_stale: boolean;
}

export interface SkillEntry {
  value: number;
  base: number;
  cap: number;
  lock: number;
}

export interface SelfState {
  serial?: HexSerial;
  name?: string;
  account?: string;
  x: number;
  y: number;
  z: number;
  direction: number;
  position_absolute: boolean;
  position_changes: number;
  hits?: number;
  hits_max?: number;
  mana?: number;
  mana_max?: number;
  stam?: number;
  stam_max?: number;
  gold?: number;
  weight?: number;
  warmode: boolean;
  notoriety?: number;
  stats: Record<string, number>;
  skills: Record<string, SkillEntry>;
  skill_names: string[];
}

export interface Mobile {
  name?: string;
  graphic?: number;
  hue?: number;
  hits?: number;
  hits_max?: number;
  mana?: number;
  mana_max?: number;
  stam?: number;
  stam_max?: number;
  notoriety?: number;
  poisoned?: boolean;
  flags?: number;
  x?: number;
  y?: number;
  z?: number;
  direction?: number;
}

export interface Item {
  name?: string;
  graphic?: number;
  amount?: number;
  x?: number;
  y?: number;
  z?: number;
  dir?: number;
  hue?: number;
  flags?: number;
  /** Parent serial (hex); absent = on the ground. */
  container?: HexSerial;
  layer?: number;
  grid?: number;
  data_type?: number;
  v11?: number;
  v12?: number;
}

export interface Gump {
  serial: HexSerial;
  gump_id: HexSerial;
  x: number;
  y: number;
  layout: string;
  lines: string[];
  open: boolean;
  compressed: boolean;
  responses: number;
}

export interface Target {
  active: boolean;
  target_type: number | null;
  cursor_id: number | null;
  cursor_type: number | null;
}

export interface CensusEntry {
  queries: number;
  sources: string[];
}

export type Buff = { icon_id: number; title?: string; description?: string } & Record<string, unknown>;

export interface Snapshot {
  self: SelfState;
  protocol_version?: number | null;
  characters: string[];
  mobiles: Record<HexSerial, Mobile>;
  items: Record<HexSerial, Item>;
  gumps: Gump[];
  target: Target;
  census: Record<HexSerial, CensusEntry>;
  names: Record<HexSerial, string>;
  /** Latest type-6 click label per serial ("Len the banker"), whole session.
   * Absent from proxies started before the field existed. */
  labels?: Record<HexSerial, string>;
  buffs: Record<HexSerial, Record<string, Buff>>;
  containers: HexSerial[];
}

/** A world-model event (runtime.py `_emit`) or a proxy event; fields vary by `ev`. */
export interface EventData {
  ev: string;
  [field: string]: unknown;
}

export type Origin = "world" | "proxy";

export interface EventEnvelope {
  seq: number;
  /** Wall-clock seconds when the proxy saw the packet. */
  t: number;
  origin: Origin;
  data: EventData;
}

/** Counter-like diagnostics. The backend sends packet_counts/unhandled as
 * [dir, id, count] rows and anomalies as {name: count}; any row form whose last
 * element is the count is accepted. */
export type Counts = Record<string, number> | Array<Array<string | number>>;

export interface Diagnostics {
  packet_counts?: Counts;
  unhandled?: Counts;
  parse_failures?: number;
  anomalies?: Counts;
  world_errors?: number;
}

export interface Playback {
  playing: boolean;
  rate: number;
  position: number;
  total: number;
}

export interface VizInfo {
  mode: "live" | "replay";
  session: string | null;
  order: "exact" | "approx" | null;
  playback: Playback | null;
  connected: boolean;
}

/** Cumulative proxy traffic since session start (independent of the event ring). */
export interface TrafficCounts {
  /** Proxy event name -> count. */
  proxy_events: Record<string, number>;
  /** [src, "0xNN", count] per non-client C2S source and packet id. */
  c2s: Array<[string, string, number]>;
}

export interface StateResponse {
  ok: boolean;
  movement: Movement;
  world: Snapshot;
  events?: EventEnvelope[];
  next: number;
  world_errors: number;
  diagnostics?: Diagnostics;
  traffic?: TrafficCounts;
  viz?: VizInfo;
  /** The proxy's agent gate; present on every response of a gate-aware proxy (also ok:false). */
  gate?: Gate;
  /** What the agent runner says it is trying to do (state-port `intent` op);
   * null/absent when no runner has reported one this session. */
  intent?: AgentIntent | null;
  /** Recent intents, oldest first (at most 30); updates of the same step are merged,
   * past steps carry `until`. The last entry is the current intent unless it was cleared. */
  intents?: AgentIntent[];
}

/** An agent runner's current intent (harness/proxy.py SessionTap.set_intent).
 * Proxy-side display data only; nothing reaches the server. */
export interface AgentIntent {
  text: string;
  /** Phase key, e.g. to_tree, chop, captcha, lockout, convert, to_inn, enter_room,
   * to_box, store, exit_room, trip_done, done, stopped. */
  kind?: string;
  /** Tile the agent is heading to / working on. */
  target?: Tile;
  loop?: string;
  trip?: number;
  trips?: number;
  /** Proxy wall clock (epoch s) when this step started (kept across merged updates). */
  since: number;
  /** History entries only: when the next intent replaced it (or it was cleared). */
  until?: number;
}

/** Precedence killed > paused > break > budget_exhausted > running. */
export type GateState = "running" | "paused" | "break" | "budget_exhausted" | "killed";

/** The proxy's agent gate (harness/agent_gate.py). While blocked, every agent
 * injection on the control port gets `ERR <reason>`; nothing reaches the server. */
export interface Gate {
  state: GateState;
  blocked: boolean;
  /** The ERR text agents get; null while running. */
  reason: string | null;
  paused: boolean;
  killed: boolean;
  /** Wall-clock (epoch s) end of the current scheduled break. */
  break_until: number | null;
  /** Agent-active seconds left before the next forced break; null during a break. */
  next_break_in_s: number | null;
  active_today_s: number;
  daily_cap_s: number;
  daily_remaining_s: number;
  /** YYYY-MM-DD */
  day: string;
  /** Proxy wall clock (epoch s) when the gate was read. */
  now: number;
}

/** What the viz may ask of the gate. `rearm` is CLI-only by policy. */
export type GateAction = "pause" | "resume" | "kill";

/** `/api/gate` reply: the proxy's JSON verbatim, or viz_server's own error. */
export interface GateResponse {
  ok: boolean;
  error?: string;
  gate?: Gate;
}

export type Tile = [number, number];

/** harness/data/walkmem.json as written by nav.WalkMemory.save:
 * tiles [x, y]; edges [x1, y1, x2, y2]; blocked [x, y, dir]. */
export interface WalkMemoryFile {
  version?: number;
  tiles: number[][];
  edges: Array<number[] | number[][]>;
  blocked: Array<number[] | [number[], number]>;
}

export type PlaybackAction =
  | { action: "play" }
  | { action: "pause" }
  | { action: "step" }
  | { action: "rate"; rate: number };

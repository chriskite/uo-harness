// Pure event helpers: EventLog filtering, click labels, the running aggregates
// (last agent action, movement log, trail, live walk memory) that outlive the
// 500-entry display ring, and the view of the state's cumulative traffic counters.
import { SERIAL_FIELDS, toHex } from "./serial.ts";
import type { EventData, EventEnvelope, HexSerial, Snapshot, Tile, TrafficCounts } from "./types.ts";

export const EVENT_RING = 500;
export const TRAIL_LEN = 50;
export const MOVEMENT_LOG_LEN = 40;

// ---------------------------------------------------------------- filter

export interface EventFilter {
  /** ev names not shown. */
  hidden: ReadonlySet<string>;
  origin: "all" | "world" | "proxy";
  /** Case-insensitive substring over ev name and fields. */
  text: string;
  /** Only events mentioning this serial. */
  serial: HexSerial | null;
}

export const DEFAULT_FILTER: EventFilter = {
  hidden: new Set(["keepalive"]),
  origin: "all",
  text: "",
  serial: null,
};

/** Serials (hex) an event refers to: the SERIAL_FIELDS plus `names` entries. */
export function eventSerials(data: EventData): HexSerial[] {
  const out: HexSerial[] = [];
  for (const [k, v] of Object.entries(data)) {
    if (SERIAL_FIELDS[k]) {
      const h = toHex(v);
      if (h) out.push(h);
    }
  }
  if (data.ev === "names" && Array.isArray(data.entries)) {
    for (const e of data.entries as Array<{ serial?: unknown }>) {
      const h = toHex(e.serial);
      if (h) out.push(h);
    }
  }
  return out;
}

export function filterEvents(events: readonly EventEnvelope[], f: EventFilter): EventEnvelope[] {
  const text = f.text.trim().toLowerCase();
  return events.filter((e) => {
    if (f.hidden.has(e.data.ev)) return false;
    if (f.origin !== "all" && e.origin !== f.origin) return false;
    if (f.serial !== null && !eventSerials(e.data).includes(f.serial)) return false;
    if (text && !JSON.stringify(e.data).toLowerCase().includes(text)) return false;
    return true;
  });
}

/** Observed ev names with counts, sorted by name: the filter vocabulary. */
export function evVocabulary(events: readonly EventEnvelope[]): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const e of events) counts.set(e.data.ev, (counts.get(e.data.ev) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

// ---------------------------------------------------------------- labels

/** A type-6 `speech_heard` is the click label the server sends for an entity
 * ("Len the banker"); returns it keyed by hex serial, else null. */
export function speechLabel(data: EventData): { serial: HexSerial; text: string } | null {
  if (data.ev !== "speech_heard" || data.type !== 6) return null;
  const serial = toHex(data.serial);
  const text = typeof data.text === "string" ? data.text.trim() : "";
  if (serial === null || text === "") return null;
  return { serial, text };
}

/** Map/inspector caption: the click label when it already names the entity
 * ("Len the banker"), name plus label when it doesn't, else whichever exists. */
export function entityCaption(name: string | undefined, label: string | undefined): string | null {
  if (label && name && !label.includes(name)) return `${name} (${label})`;
  return label ?? name ?? null;
}

// ---------------------------------------------------------------- aggregates

/** Proxy events shown in the MovementPanel log. */
export const MOVEMENT_EVS: ReadonlySet<string> = new Set([
  "step",
  "blocked",
  "walk_rejected",
  "resync_ignored",
  "reanchor_client",
  "c2s_token_stamped",
  "c2s_token_mismatch",
  "c2s_stale_token_dropped",
  "c2s_token_passthrough",
  "c2s_agent_key_cleared",
  "c2s_resync_seen",
]);

/** World events produced by C2S packets: the detail of an agent packet. */
const C2S_WORLD_EVS: ReadonlySet<string> = new Set([
  "walk",
  "speech",
  "dclick",
  "query",
  "item_query",
  "gump_response",
  "target_response",
  "spell_cast",
  "char_select",
  "login",
]);

export interface AgentAction {
  seq: number;
  t: number;
  src: string;
  id: string;
  /** The world event decoded from the same packet, when paired. */
  detail: EventData | null;
}

export interface LiveWalk {
  steps: Array<[Tile, Tile]>;
  blocked: Array<[Tile, number]>;
}

export interface Aggregates {
  /** Latest type-6 label per serial seen in the stream: a live-update fallback
   * for `world.labels`, which is authoritative (see labelOf). */
  labels: Record<HexSerial, string>;
  trail: Tile[];
  live: LiveWalk;
  movementLog: EventEnvelope[];
  lastAgent: AgentAction | null;
  /** Last world event decoded from a C2S packet (pairing aid for lastAgent). */
  lastC2SWorld: EventEnvelope | null;
}

export function emptyAggregates(): Aggregates {
  return {
    labels: {},
    trail: [],
    live: { steps: [], blocked: [] },
    movementLog: [],
    lastAgent: null,
    lastC2SWorld: null,
  };
}

function tile(v: unknown): Tile | null {
  if (Array.isArray(v) && typeof v[0] === "number" && typeof v[1] === "number") return [v[0], v[1]];
  return null;
}

/** Same packet: identical proxy timestamp, adjacent in the event log. */
const samePacket = (a: EventEnvelope, b: { seq: number; t: number }) =>
  a.t === b.t && Math.abs(a.seq - b.seq) <= 4;

/** Fold one envelope into the aggregates (mutates `agg`). */
export function foldEvent(agg: Aggregates, env: EventEnvelope): void {
  const d = env.data;
  if (env.origin === "world") {
    const label = speechLabel(d);
    if (label) agg.labels[label.serial] = label.text;
    if (C2S_WORLD_EVS.has(d.ev)) {
      agg.lastC2SWorld = env;
      const last = agg.lastAgent;
      if (last && last.detail === null && samePacket(env, last)) last.detail = d;
    }
    return;
  }
  if (MOVEMENT_EVS.has(d.ev)) {
    agg.movementLog.push(env);
    if (agg.movementLog.length > MOVEMENT_LOG_LEN) agg.movementLog.shift();
  }
  switch (d.ev) {
    case "c2s": {
      const src = String(d.src ?? "?");
      if (src !== "client") {
        const c2sWorld = agg.lastC2SWorld;
        agg.lastAgent = {
          seq: env.seq,
          t: env.t,
          src,
          id: String(d.id ?? "?"),
          detail: c2sWorld && samePacket(c2sWorld, env) ? c2sWorld.data : null,
        };
      }
      break;
    }
    case "step": {
      const from = tile(d.from);
      const to = tile(d.to);
      if (from && to) {
        agg.live.steps.push([from, to]);
        agg.trail.push(to);
        if (agg.trail.length > TRAIL_LEN) agg.trail.shift();
      }
      break;
    }
    case "blocked": {
      const from = tile(d.from);
      if (from && typeof d.dir === "number") agg.live.blocked.push([from, d.dir]);
      break;
    }
  }
}

/** Copy with fresh containers, so React sees a new value after in-place folds. */
export function cloneAggregates(a: Aggregates): Aggregates {
  return {
    labels: { ...a.labels },
    trail: [...a.trail],
    live: { steps: [...a.live.steps], blocked: [...a.live.blocked] },
    movementLog: [...a.movementLog],
    lastAgent: a.lastAgent && { ...a.lastAgent },
    lastC2SWorld: a.lastC2SWorld,
  };
}

/** Click label for a serial: the snapshot's session-long `world.labels` first,
 * then the stream fold (covers the gap until the next state frame). */
export function labelOf(world: Snapshot | null | undefined, streamLabels: Record<HexSerial, string>, serial: HexSerial): string | undefined {
  return world?.labels?.[serial] ?? streamLabels[serial];
}

export interface TrafficSummary {
  /** Non-client C2S packets: src -> packet id -> count. */
  bySrc: Record<string, Record<string, number>>;
  confirmsHidden: number;
  confirmsRewritten: number;
  /** reanchor_client = fabricated S2C 0x21 sent to the client only. */
  reanchors: number;
  /** c2s_token_* / stale-token / agent-key events by name. */
  tokenEvents: Record<string, number>;
}

const TOKEN_EVS: Record<string, true> = { c2s_stale_token_dropped: true, c2s_agent_key_cleared: true };

/** Panel view of the state response's cumulative `traffic` counters. */
export function trafficSummary(counts: TrafficCounts): TrafficSummary {
  const bySrc: TrafficSummary["bySrc"] = {};
  for (const [src, id, n] of counts.c2s) (bySrc[src] ??= {})[id] = n;
  const ev = counts.proxy_events;
  const tokenEvents: Record<string, number> = {};
  for (const [name, n] of Object.entries(ev)) {
    if (name.startsWith("c2s_token") || TOKEN_EVS[name]) tokenEvents[name] = n;
  }
  return {
    bySrc,
    confirmsHidden: ev.s2c_confirm_hidden ?? 0,
    confirmsRewritten: ev.s2c_confirm_rewritten ?? 0,
    reanchors: ev.reanchor_client ?? 0,
    tokenEvents,
  };
}

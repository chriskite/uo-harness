// Event text and the Sim timeline's marker rows (shared by the timeline, the [ / ] keys, the inspector
// and the runner panel).
import type { Trace } from "./trace.ts";
import type { TraceEvent } from "./types.ts";

export const MARKER_ROWS = ["death", "hurt", "cast", "potion", "vet", "recall", "ability", "log"] as const;
export type MarkerRow = (typeof MARKER_ROWS)[number];

export const ROW_LABEL: Record<MarkerRow, string> = {
  death: "deaths",
  hurt: "player hurt ≥20",
  cast: "casts at player",
  potion: "potions",
  vet: "vet",
  recall: "recall",
  ability: "abilities",
  log: "runner log",
};

export const ROW_COLOR: Record<MarkerRow, string> = {
  death: "#9ca3af",
  hurt: "#ef4444",
  cast: "#a78bfa",
  potion: "#22c55e",
  vet: "#14b8a6",
  recall: "#3b82f6",
  ability: "#fb923c",
  log: "#facc15",
};

export interface Marker {
  t: number;
  row: MarkerRow;
  color: string;
  ev: TraceEvent;
}

const LOG_MARK = /LEAVING|death|fled|retreat/i;
/** Events about the player without a mobile field. */
const PLAYER_EVENTS = new Set(["potion", "vet", "recall", "song", "bard", "intent", "sys", "speech", "log"]);

/** Whether `e` names mobile `s` (as attacker, victim, subject or target), or is the player's own. */
export function involves(e: TraceEvent, s: number, player: number | undefined): boolean {
  return e.by === s || e.v === s || e.m === s || e.target === s || (s === player && PLAYER_EVENTS.has(e.e));
}

function rowOf(e: TraceEvent, player: number | undefined): MarkerRow | null {
  switch (e.e) {
    case "die":
      return "death";
    case "hurt":
      return e.v === player && (e.n ?? 0) >= 20 ? "hurt" : null;
    case "cast":
      return e.v === player ? "cast" : null;
    case "potion":
    case "vet":
    case "recall":
    case "ability":
      return e.e;
    case "log":
      return e.text && LOG_MARK.test(e.text) ? "log" : null;
    default:
      return null;
  }
}

/** The markers of the enabled rows, in time order; `only` keeps those involving that mobile. */
export function markers(trace: Trace, rows: ReadonlySet<MarkerRow>, only: number | null): Marker[] {
  const player = trace.head?.player;
  const out: Marker[] = [];
  for (const e of trace.events) {
    const row = rowOf(e, player);
    if (!row || !rows.has(row)) continue;
    if (only !== null && !involves(e, only, player)) continue;
    let color = ROW_COLOR[row];
    if (row === "death") {
      const kind = e.m != null ? trace.names.get(e.m)?.kind : undefined;
      color = kind === "player" ? "#ef4444" : kind === "pet" ? "#f97316" : ROW_COLOR.death;
    }
    out.push({ t: e.t, row, color, ev: e });
  }
  return out;
}

const r1 = (n: number | undefined) => (n === undefined ? "?" : n.toFixed(1));

/** One line describing an event, with names from the trace. */
export function describeEvent(e: TraceEvent, tr: Trace): string {
  const N = (s: number | null | undefined) => tr.nameOf(s);
  switch (e.e) {
    case "hurt":
      return `${N(e.v)} -${e.n} (${e.kind}) by ${e.by != null ? N(e.by) : e.kind} → ${e.h}`;
    case "heal":
      return `${N(e.m)} +${e.n} → ${e.h}`;
    case "die":
      return `${N(e.m)} dies${e.by != null ? ` (${N(e.by)})` : ""}`;
    case "swing":
      return `${N(e.by)} swings at ${N(e.v)}: ${e.hit ? "hit" : "miss"}`;
    case "cast":
      return `${N(e.by)} casts${e.spell ? ` ${e.spell}` : ""} at ${N(e.v)} (ends ${r1(e.end)})`;
    case "release":
      return `${N(e.by)} releases${e.spell ? ` ${e.spell}` : ""} at ${N(e.v)} (lands ${r1(e.land)})`;
    case "cast_lost":
      return `${N(e.by)} loses its cast at ${N(e.v)}: ${e.why}`;
    case "ability":
      return `${N(e.by)} ${e.kind}${e.v != null ? ` → ${N(e.v)}` : ""}`;
    case "bard":
      return `${e.skill} ${e.v != null ? `on ${N(e.v)} ` : ""}${typeof e.ok === "number" ? `(${e.ok} calmed)` : e.ok ? "ok" : "failed"}`;
    case "song":
      return `song ${e.kind} until ${r1(e.until)}`;
    case "potion":
      return `${e.kind} potion ${e.ok ? "ok" : "failed"}${e.n ? ` (${e.n})` : ""}`;
    case "vet":
      return `vet ${e.phase}${e.phase === "start" ? ` (ends ${r1(e.end)})` : ""}`;
    case "recall":
      return `recall ${e.phase}${e.dest != null ? ` → ${typeof e.dest === "string" ? e.dest : JSON.stringify(e.dest)}` : ""}`;
    case "spawn":
      return `spawn ${N(e.m)} (${e.key})`;
    case "say":
      return `${N(e.m)}: ${e.text}`;
    case "sys":
    case "speech":
    case "log":
      return `${e.e}: ${e.text}`;
    case "intent":
      return `intent ${e.kind}${e.text ? `: ${e.text}` : ""}${e.target != null ? ` → ${N(e.target)}` : ""}`;
    default:
      return e.e;
  }
}

/** Sim seconds -> "mm:ss.s". */
export function fmtSimT(t: number): string {
  const s = Math.max(0, t);
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  return `${String(m).padStart(2, "0")}:${rest.toFixed(1).padStart(4, "0")}`;
}

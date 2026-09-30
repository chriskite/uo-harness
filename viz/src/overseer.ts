// Overseer panel model (docs/VISUALIZER.md §2.4, docs/OVERSEER.md): the
// /api/overseer payload, cursor-based merging of polled rows, and the single
// chat + juncture timeline the panel renders.

export type ChatRole = "user" | "overseer" | "system";
export type ChatKind = "message" | "thought" | "action" | "memory";
export type Severity = "info" | "attention" | "urgent";

export interface ChatRow {
  id: number;
  t: number;
  role: ChatRole;
  kind: ChatKind;
  text: string;
  data: Record<string, unknown>;
}

export interface Juncture {
  id: number;
  t: number;
  source: string;
  kind: string;
  severity: Severity;
  summary: string;
  data: Record<string, unknown>;
  acked_t: number | null;
}

export interface OverseerResponse {
  chat: ChatRow[];
  junctures: Juncture[];
  open: number;
  open_ids: number[];
  /** Epoch seconds of the overseer's last `ctl` poll (meta overseer_heartbeat), null if never. */
  heartbeat: number | null;
  now: number;
  store: boolean;
}

/** An overseer heartbeat younger than this counts as a running session. */
export const HEARTBEAT_FRESH_S = 90;
export const CHAT_MAX_CHARS = 2000;
/** Rows kept in the panel (oldest dropped first). */
export const TIMELINE_KEEP = 500;

export interface OverseerState {
  chat: ChatRow[];
  junctures: Juncture[];
  openIds: number[];
  heartbeat: number | null;
  /** Server clock at the last poll (heartbeat ages are measured against it). */
  now: number | null;
  store: boolean;
  afterChat: number;
  afterJuncture: number;
}

export const EMPTY_OVERSEER: OverseerState = {
  chat: [],
  junctures: [],
  openIds: [],
  heartbeat: null,
  now: null,
  store: true,
  afterChat: 0,
  afterJuncture: 0,
};

/** Append new rows by id (duplicates from overlapping polls dropped), advance the cursors. */
export function mergeOverseer(s: OverseerState, r: OverseerResponse): OverseerState {
  const addNew = <T extends { id: number }>(have: T[], got: T[], after: number): T[] => {
    const fresh = got.filter((x) => x.id > after);
    return fresh.length ? [...have, ...fresh].slice(-TIMELINE_KEEP) : have;
  };
  const chat = addNew(s.chat, r.chat, s.afterChat);
  const junctures = addNew(s.junctures, r.junctures, s.afterJuncture);
  return {
    chat,
    junctures,
    openIds: r.open_ids,
    heartbeat: r.heartbeat,
    now: r.now,
    store: r.store,
    afterChat: Math.max(s.afterChat, ...r.chat.map((c) => c.id)),
    afterJuncture: Math.max(s.afterJuncture, ...r.junctures.map((j) => j.id)),
  };
}

export type TimelineItem =
  | { type: "chat"; key: string; t: number; row: ChatRow }
  | { type: "juncture"; key: string; t: number; row: Juncture; open: boolean };

/** Chat rows and junctures in time order (ties: chat before juncture, then id).
 * Acked junctures are left out unless `showAcked`. */
export function timeline(s: OverseerState, showAcked = false): TimelineItem[] {
  const open = new Set(s.openIds);
  const items: TimelineItem[] = [
    ...s.chat.map((row) => ({ type: "chat" as const, key: `c${row.id}`, t: row.t, row })),
    ...s.junctures
      .filter((row) => showAcked || open.has(row.id))
      .map((row) => ({ type: "juncture" as const, key: `j${row.id}`, t: row.t, row, open: open.has(row.id) })),
  ];
  return items.sort((a, b) => a.t - b.t || (a.type === b.type ? a.row.id - b.row.id : a.type === "chat" ? -1 : 1));
}

/** "12s", "5m", "3h 20m", "2d 4h" (floored; negatives clamp to 0s). */
export function fmtAgo(s: number): string {
  const t = Math.max(0, Math.floor(s));
  if (t < 60) return `${t}s`;
  const m = Math.floor(t / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${String(m % 60).padStart(2, "0")}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

/** Whether an overseer session is running, from its heartbeat against the server clock. */
export function overseerStatus(heartbeat: number | null, now: number | null): { active: boolean; text: string } {
  if (heartbeat === null) return { active: false, text: "no overseer running (never seen)" };
  const age = (now ?? heartbeat) - heartbeat;
  if (age < HEARTBEAT_FRESH_S) return { active: true, text: "overseer active" };
  return { active: false, text: `no overseer running (last seen ${fmtAgo(age)} ago)` };
}

/** Client-side mirror of POST /api/chat validation: error text, or null when sendable. */
export function chatError(text: string): string | null {
  const n = text.trim().length;
  if (n === 0) return "empty";
  if (n > CHAT_MAX_CHARS) return `${n - CHAT_MAX_CHARS} characters over ${CHAT_MAX_CHARS}`;
  return null;
}

/** First line of a thought, cut to `max` chars, for its collapsed summary. */
export function thoughtPreview(text: string, max = 90): string {
  const line = text.trim().split(/\r?\n/, 1)[0] ?? "";
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

/** A knowledge entry as carried in a memory row (harness/ctl.py `_compact`). */
export interface MemoryEntry {
  id: number;
  kind: string;
  topic: string;
  content: string;
  confidence?: number;
  importance?: number;
  score?: number;
  status?: string;
  similarity?: number;
}

export interface MemoryView {
  op: string;
  /** "recall" (search/brief/get/review: reading memory) or "write" (add/update/confirm/retract). */
  mode: "recall" | "write";
  headline: string;
  /** Result lists to show, in order; empty groups are left out. */
  groups: { label: string; entries: MemoryEntry[] }[];
  /** Number of entries across groups (for the collapsed summary). */
  count: number;
}

const RECALL_OPS: Record<string, true> = { search: true, brief: true, get: true, review: true };

function entries(v: unknown): MemoryEntry[] {
  return Array.isArray(v) ? (v.filter((e) => e && typeof e === "object" && "id" in e) as MemoryEntry[]) : [];
}

/** How a `memory` chat row (a `ctl know` call) is shown: a headline (the row's
 * text) and its entry lists: results for a lookup; the entry plus related ones
 * for a write. */
export function memoryView(row: ChatRow): MemoryView {
  const d = row.data ?? {};
  const op = typeof d.op === "string" ? d.op : "?";
  const groups: { label: string; entries: MemoryEntry[] }[] = [];
  if (op === "brief") {
    groups.push({ label: "relevant here", entries: entries(d.relevant) });
    groups.push({ label: "standing rules", entries: entries(d.standing) });
  } else if (op === "search" || op === "review") {
    groups.push({ label: op === "review" ? "unconfirmed guesses" : "results", entries: entries(d.results) });
  } else {
    // an add's headline already carries the content; other writes (confirm/update/retract/get) show the entry
    if (d.entry && op !== "add") groups.push({ label: "entry", entries: entries([d.entry]) });
    groups.push({ label: "related (possible conflicts)", entries: entries(d.related) });
  }
  const shown = groups.filter((g) => g.entries.length > 0);
  return {
    op,
    mode: RECALL_OPS[op] ? "recall" : "write",
    headline: row.text,
    groups: shown,
    count: shown.reduce((n, g) => n + g.entries.length, 0),
  };
}

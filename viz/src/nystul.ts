// Nystul the Wizard, the read-only assistant chat (harness/nystul.py behind
// /api/nystul*): response types and pure helpers for the chat components.

export interface NystulStep {
  tool: string;
  args: Record<string, unknown>;
  /** Epoch seconds. */
  t: number;
  /** null while the tool is still running. */
  ok: boolean | null;
}

export type NystulStatus = "done" | "running" | "error" | "cancelled";

export interface NystulMessage {
  id: number;
  role: "user" | "nystul";
  t: number;
  text: string;
  status: NystulStatus;
  steps: NystulStep[];
  model: string | null;
  cost: number | null;
  tokens_in: number | null;
  tokens_out: number | null;
  seconds: number | null;
  error: string | null;
}

/** GET /api/nystul/<id>. */
export interface NystulConversation {
  conversation: { id: number; title: string; t_created: number; t_updated: number };
  messages: NystulMessage[];
}

export interface NystulListItem {
  id: number;
  title: string;
  t_updated: number;
  messages: number;
  running: boolean;
}

/** GET /api/nystul: conversations newest first; `available` = omp found on the server. */
export interface NystulList {
  conversations: NystulListItem[];
  available: boolean;
  model: string;
  thinking: string;
}

export const NYSTUL_MAX_CHARS = 4000;

/** Client-side mirror of POST /api/nystul/ask validation: error text, or null when sendable. */
export function askError(text: string): string | null {
  const n = text.trim().length;
  if (n === 0) return "empty";
  if (n > NYSTUL_MAX_CHARS) return `${n - NYSTUL_MAX_CHARS} characters over ${NYSTUL_MAX_CHARS}`;
  return null;
}

/** In-character verbs for the tools (harness/nystul_ext.ts). */
export const STEP_VERBS: Record<string, string> = {
  uo_api: "gazed into the scrying pool",
  uo_ctl: "consulted the Seer's instruments",
  uo_sql: "searched the archives",
  uo_knowledge: "consulted the Codex",
  uo_discord: "listened to the town criers",
  uo_read: "read a tome",
  uo_grep: "searched the tomes",
  uo_list: "browsed the library",
};

const STEP_ARG_KEYS = ["route", "query", "path", "pattern", "command"];
const STEP_ARG_MAX = 60;

/** Verb plus the first string argument among route/query/path/pattern/command, capped. */
export function stepLabel(step: NystulStep): string {
  const verb = STEP_VERBS[step.tool] ?? step.tool;
  const arg = STEP_ARG_KEYS.map((k) => step.args[k]).find((v): v is string => typeof v === "string" && v !== "");
  if (arg === undefined) return verb;
  return `${verb}: ${arg.length > STEP_ARG_MAX ? `${arg.slice(0, STEP_ARG_MAX - 1)}…` : arg}`;
}

/** "12 s · 5 lookups · $0.04"; unknown (null) parts are left out. */
export function runSummary(msg: NystulMessage): string {
  const parts: string[] = [];
  if (msg.seconds !== null) parts.push(`${Math.round(msg.seconds)} s`);
  parts.push(`${msg.steps.length} ${msg.steps.length === 1 ? "lookup" : "lookups"}`);
  if (msg.cost !== null) parts.push(`$${msg.cost.toFixed(2)}`);
  return parts.join(" · ");
}

export function isRunning(conv: NystulConversation | null): boolean {
  return conv !== null && conv.messages.some((m) => m.status === "running");
}

// Nystul the Wizard, the assistant chat (harness/nystul.py behind /api/nystul*): it only
// looks things up, plus Codex changes the operator approves. Response types and pure helpers.

export interface NystulStep {
  tool: string;
  args: Record<string, unknown>;
  /** Epoch seconds. */
  t: number;
  /** null while the tool is still running. */
  ok: boolean | null;
}

export type NystulStatus = "done" | "running" | "error" | "cancelled";

/** A knowledge entry as the server snapshots it (harness/nystul_memory.py compact). */
export interface ProposalEntry {
  id: number;
  kind: string;
  topic: string;
  content: string;
  confidence: number;
  importance: number;
  status: string;
  source_type: string;
  source_ref: string | null;
  tags: string[];
}

/** A memory-store change Nystul proposed (uo_propose); only Approve applies it. */
export interface NystulProposal {
  id: number;
  t: number;
  /** The uo_propose arguments: op, why, and the entry fields. */
  params: Record<string, unknown>;
  /** The entries it changes as they were when proposed, keyed by id; null = gone. */
  before: Record<string, ProposalEntry | null>;
  status: "pending" | "applying" | "applied" | "failed";
  t_decided: number | null;
  result: { id: number; action: string } | null;
  error: string | null;
}

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
  proposals: NystulProposal[];
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
  uo_propose: "drafted a Codex change",
};

const STEP_ARG_KEYS = ["route", "query", "path", "pattern", "command", "topic"];
const STEP_ARG_MAX = 60;

/** Verb plus the first string argument among route/query/path/pattern/command/topic, capped. */
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

const PROPOSAL_FIELDS: [key: string, label: string][] = [
  ["kind", "kind"],
  ["topic", "topic"],
  ["content", "content"],
  ["tags", "tags"],
  ["entities", "about"],
  ["source", "source"],
  ["importance", "importance"],
  ["confidence", "confidence"],
  ["ref", "evidence"],
  ["supersedes", "replaces"],
];

export interface ProposalView {
  /** "Add a fact", "Update #12", "Retract #12". */
  title: string;
  why: string;
  /** The entry it changes, as it was when proposed (update/retract; add with supersedes). */
  before: ProposalEntry | null;
  /** Proposed values, in a fixed order; absent or empty ones are left out. */
  fields: { label: string; value: string }[];
  /** What the card says instead of the button; null while it can be approved. */
  outcome: string | null;
}

function fieldText(v: unknown): string | null {
  if (v === null || v === undefined || v === "") return null;
  if (Array.isArray(v)) return v.length ? v.map(String).join(", ") : null;
  if (typeof v === "number") return v === Math.trunc(v) ? String(v) : v.toFixed(2);
  return String(v);
}

export function proposalView(p: NystulProposal): ProposalView {
  const op = String(p.params.op ?? "?");
  const id = typeof p.params.id === "number" ? p.params.id : null;
  const kind = typeof p.params.kind === "string" ? p.params.kind : "entry";
  const title = op === "add" ? `Add a ${kind}` : op === "update" ? `Update #${id}` : op === "retract" ? `Retract #${id}` : op;
  const target = id ?? (typeof p.params.supersedes === "number" ? p.params.supersedes : null);
  const before = target !== null ? (p.before[String(target)] ?? null) : null;
  const fields =
    op === "retract"
      ? []
      : PROPOSAL_FIELDS.flatMap(([key, label]) => {
          const value = fieldText(p.params[key]);
          return value === null || (key === "kind" && op === "add") ? [] : [{ label, value }];
        });
  const outcome =
    p.status === "applied"
      ? `approved: ${p.result?.action ?? "applied"} as #${p.result?.id ?? "?"}`
      : p.status === "failed"
        ? `failed: ${p.error ?? "unknown error"}`
        : p.status === "applying"
          ? "applying…"
          : null;
  return { title, why: String(p.params.why ?? ""), before, fields, outcome };
}

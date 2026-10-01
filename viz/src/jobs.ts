// Jobs page model (docs/VISUALIZER.md §2.4): the /api/jobs payload
// (harness/jobs.py) and the pure formatting/aggregation helpers the dashboard uses.
import { fmtDuration } from "./format.ts";

export type Tone = "ok" | "warn" | "bad" | "info" | "dim";

export interface JobTrip {
  /** 1-based over all trips in the answer (trip is the runner's own per-run number). */
  n: number;
  trip: number | null;
  venue: string | null;
  t_start: number | null;
  t_end: number | null;
  duration_s: number | null;
  logs: number;
  stored: number;
  logs_per_hour: number | null;
  captchas: number;
  captcha_wait_s: number;
  attempts: number;
  successes: number;
  phases_s: Record<string, number>;
  steps: number | null;
  blocked: number | null;
  /** {wood name: logs}; empty when the trip row carries no breakdown. */
  woods: Record<string, number>;
  value_gp: number | null;
  value_unpriced_logs: number;
  /** job_events kinds that happened during the trip, counted. */
  events: Record<string, number>;
}

export interface JobAgg {
  trips: number;
  logs: number;
  stored: number;
  active_s: number;
  active_hours: number;
  logs_per_hour: number | null;
  logs_per_trip: number | null;
  captchas: number;
  captcha_wait_s: number;
  attempts: number;
  successes: number;
  success_rate: number | null;
  woods: Record<string, number>;
  deaths: { pk: number; mob: number; other: number; total: number };
  thefts: { count: number; amount: number; items: Record<string, number> };
  pk_seen: number;
  flees: number;
  value_gp: number | null;
  value_unpriced_logs: number;
}

export interface JobTotals extends JobAgg {
  first_t: number | null;
  last_t: number | null;
}

export interface JobDay extends JobAgg {
  day: string;
}

export interface RollingPoint {
  t: number;
  n: number;
  logs_per_hour: number | null;
  cum_logs_per_hour: number | null;
  window_trips: number;
}

export interface JobEvent {
  id: number | null;
  t: number;
  kind: string;
  facet: number | null;
  x: number | null;
  y: number | null;
  data: Record<string, unknown>;
}

export interface WoodRow {
  name: string;
  logs: number;
  value_gp: number | null;
  total_gp: number | null;
  min_skill?: number | null;
  known: boolean;
}

export interface HarvestStats {
  success: number;
  fail: number;
  depleted: number;
  unreachable: number;
  not_tree: number;
  yield: number;
  success_rate: number | null;
}

export interface JobsResponse {
  job: string;
  since: number;
  utc_offset_s: number;
  window_s: number;
  woods_file: boolean;
  /** false when the memory store file does not exist (everything empty). */
  store: boolean;
  trips: JobTrip[];
  totals: JobTotals;
  days: JobDay[];
  rolling: RollingPoint[];
  events: JobEvent[];
  woods: WoodRow[];
  harvest: HarvestStats | null;
}

export const PHASES = ["harvest", "convert", "to_bank", "store"] as const;

/** Number or an em dash for unknown; `digits` decimals, trailing zeros dropped. */
export function fmtNum(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return String(Number(v.toFixed(digits)));
}

export function fmtGp(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${Math.round(v).toLocaleString("en-US")} gp`;
}

/** "0.24 h" plus "14m" / "2h 05m" for the active-time tile. */
export function fmtHours(s: number): { value: string; sub: string } {
  const m = Math.floor(Math.max(0, s) / 60);
  const sub = m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${m}m`;
  return { value: `${fmtNum(s / 3600, 2)} h`, sub };
}

export interface Kpi {
  key: string;
  label: string;
  value: string;
  sub?: string;
  tone: Tone;
}

/** The KPI tile row, in display order. */
export function kpis(t: JobTotals): Kpi[] {
  const hours = fmtHours(t.active_s);
  const lost = t.thefts.amount;
  return [
    { key: "lph", label: "logs / hr", value: fmtNum(t.logs_per_hour, 0), sub: `${t.logs} logs · ${t.stored} stored`, tone: "info" },
    { key: "lpt", label: "logs / trip", value: fmtNum(t.logs_per_trip, 1), sub: t.success_rate === null ? undefined : `${Math.round(t.success_rate * 100)}% chops land`, tone: "info" },
    { key: "trips", label: "trips", value: String(t.trips), tone: "dim" },
    { key: "active", label: "active hours", value: hours.value, sub: hours.sub, tone: "dim" },
    { key: "pk", label: "deaths to PKs", value: String(t.deaths.pk), sub: t.pk_seen ? `${t.pk_seen} PK sighting${t.pk_seen === 1 ? "" : "s"}` : undefined, tone: t.deaths.pk ? "bad" : "ok" },
    { key: "mob", label: "deaths to mobs", value: String(t.deaths.mob), sub: t.deaths.other ? `+${t.deaths.other} other` : undefined, tone: t.deaths.mob ? "bad" : "ok" },
    { key: "theft", label: "loss to thieves", value: String(lost), sub: `${t.thefts.count} theft${t.thefts.count === 1 ? "" : "s"}`, tone: lost || t.thefts.count ? "warn" : "ok" },
    { key: "captcha", label: "captchas", value: String(t.captchas), sub: t.captchas ? `${fmtDuration(t.captcha_wait_s)} waiting` : undefined, tone: t.captchas ? "warn" : "ok" },
  ];
}

export interface EventView {
  label: string;
  detail: string;
  tone: Tone;
}

function str(v: unknown): string | null {
  return typeof v === "string" && v ? v : null;
}

/** Timeline wording for one job event. */
export function eventView(e: JobEvent): EventView {
  const d = e.data;
  const who = str(d.name) ?? str(d.by) ?? str(d.attacker);
  const at = e.x !== null && e.y !== null ? `${e.x},${e.y}` : "";
  const detail = [who, at].filter(Boolean).join(" · ");
  switch (e.kind) {
    case "death": {
      const cause = d.cause === "pk" ? "PK" : d.cause === "mob" ? "monster" : str(d.cause) ?? "unknown cause";
      return { label: `Died (${cause})`, detail, tone: "bad" };
    }
    case "theft": {
      const { amount, items } = theftLoss(d);
      const what = Object.entries(items)
        .map(([k, n]) => `${n} ${k}`)
        .join(", ");
      return { label: `Theft: ${amount} lost`, detail: [what, detail].filter(Boolean).join(" · "), tone: "warn" };
    }
    case "pk_seen":
      return { label: "PK seen", detail, tone: "warn" };
    case "flee":
      return { label: "Fled", detail: [str(d.reason), detail].filter(Boolean).join(" · "), tone: "info" };
    case "resurrect":
      return { label: "Resurrected", detail, tone: "ok" };
    case "mob_attack":
      return { label: "Attacked by a monster", detail, tone: "warn" };
    default:
      return { label: e.kind, detail, tone: "dim" };
  }
}

/** Same rule as harness/jobs.py theft_loss: data.amount when numeric, else the item counts summed. */
export function theftLoss(d: Record<string, unknown>): { amount: number; items: Record<string, number> } {
  const items: Record<string, number> = {};
  const add = (k: string, n: number) => {
    items[k] = (items[k] ?? 0) + n;
  };
  const raw = d.items;
  if (Array.isArray(raw)) {
    for (const it of raw) {
      if (it && typeof it === "object") {
        const o = it as Record<string, unknown>;
        add(String(o.name ?? o.graphic ?? "?"), typeof o.amount === "number" ? o.amount : 1);
      } else add(String(it), 1);
    }
  } else if (raw && typeof raw === "object") {
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) add(k, typeof v === "number" ? v : 1);
  }
  const amount = typeof d.amount === "number" ? d.amount : Object.values(items).reduce((a, b) => a + b, 0);
  return { amount, items };
}

export interface WoodShare {
  name: string;
  logs: number;
  share: number;
  value_gp: number | null;
  total_gp: number | null;
}

/** Woods with logs > 0, most logs first, with their share of all logs. */
export function woodShares(rows: readonly WoodRow[]): WoodShare[] {
  const withLogs = rows.filter((r) => r.logs > 0);
  const sum = withLogs.reduce((a, r) => a + r.logs, 0);
  return withLogs
    .map((r) => ({ name: r.name, logs: r.logs, share: sum ? r.logs / sum : 0, value_gp: r.value_gp, total_gp: r.total_gp }))
    .sort((a, b) => b.logs - a.logs || a.name.localeCompare(b.name));
}

/** Phase seconds of a trip in PHASES order (missing phases as 0), then any extra phases. */
export function phaseList(p: Record<string, number>): [string, number][] {
  const known = PHASES.map((k) => [k, p[k] ?? 0] as [string, number]);
  const extra = Object.entries(p).filter(([k]) => !(PHASES as readonly string[]).includes(k));
  return [...known, ...extra];
}

/** Local "MM-DD HH:MM" for trip starts; "HH:MM" when `sameDay`. */
export function fmtStamp(t: number, sameDay = false): string {
  const d = new Date(t * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`;
  return sameDay ? hm : `${p(d.getMonth() + 1)}-${p(d.getDate())} ${hm}`;
}

/** True when every stamp falls on one local calendar day. */
export function oneLocalDay(ts: readonly number[]): boolean {
  const days = new Set(ts.map((t) => new Date(t * 1000).toDateString()));
  return days.size <= 1;
}

/** The browser's offset for the per-day split, in minutes east of UTC (the `tz` query). */
export function tzMinutesEast(now = new Date()): number {
  return -now.getTimezoneOffset();
}

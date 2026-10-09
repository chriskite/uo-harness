// Jobs page model (docs/VISUALIZER.md §2.4): the /api/jobs payload
// (harness/jobs.py) and the pure formatting/aggregation helpers the dashboard uses.
import { fmtDuration } from "./format.ts";

export type Tone = "ok" | "warn" | "bad" | "info" | "dim";

export interface JobTrip {
  /** 1-based over all trips in the answer (trip is the runner's own per-run number). */
  n: number;
  trip: number | null;
  spot: string | null;
  outcome: string;
  why: string | null;
  t_start: number | null;
  t_end: number | null;
  duration_s: number | null;
  logs: number;
  stored: number;
  /** boards the Resource Stockpile confirmed taking (rows since 2026-10-05; 0 before) */
  stockpiled: number;
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
  /** {wood: the per-log price the value used (newest board:<wood> price at the trip's end, else woods.json), null = unpriced} */
  board_prices: Record<string, BoardPrice | null>;
  /** job_events kinds that happened during the trip, counted. */
  events: Record<string, number>;
  // what the lumber optimizer learns from (harness/jobs.py _trip_extra; null in older rows)
  field_s: number | null;
  field_logs_per_hour: number | null;
  place_fail: boolean;
  time_split: TimeSplit | null;
  walk_out_s: number | null;
  chop_s: number | null;
  lockout_s: number | null;
  travel_s: number | null;
  travel: TripLeg[];
  speech_wait_s: number;
  stationary_clears: number;
  escapes: number;
  skill: number | null;
  skill_end: number | null;
  supplies: Supplies | null;
  players_seen: number | null;
  dry: boolean;
  hatchet: { material: string | null; quality: string | null; serial: string | null } | null;
  hatchet_uses_seen: { n: number; t: number } | null;
}

/** Seconds of a trip: recall legs (and the walk to the library), the travel lockout, field time, the rest. */
export interface TimeSplit {
  travel: number;
  lockout: number;
  field: number;
  other: number;
}

/** One travel leg as the trip row keeps it (loop_lumber.leg_summary). */
export interface TripLeg {
  leg: string;
  kind?: string;
  method?: string;
  book?: string;
  witcher_rune?: string;
  ok: boolean;
  attempts?: number;
  s?: number;
  walk_s?: number;
  failure?: string;
  charges?: number;
  mana_used?: number;
  reagents_used?: Record<string, number>;
  /** [method, failure or null, seconds] per cast */
  tries: [string | null, string | null, number | null][];
}

export interface Supplies {
  library_charges: number;
  own_charges: number;
  recall_casts: number;
  reagents_used?: Record<string, number>;
}

export interface SuppliesTotal extends Supplies {
  trips: number;
  reagents_used: Record<string, number>;
  /** priced with the store's prices (reagent:<name>, recall_charge); unpriced = units without a price */
  gp?: number;
  unpriced?: number;
}

export interface LegStats {
  leg: string;
  n: number;
  ok: number;
  casts: number;
  charge: number;
  spell: number;
  failures: Record<string, number>;
  mean_s: number | null;
  mean_walk_s: number | null;
}

export interface BookStats {
  book: string;
  kind: string | null;
  library: string | null;
  own: boolean;
  uses: number;
  runes: string[];
  /** [t, charges shown before the recall] */
  charges: [number, number][];
  last_charges: number | null;
  last_t: number | null;
  min_charges: number | null;
}

export interface PlanSpot {
  id: string;
  name: string | null;
  status: string;
  eligible: boolean;
  why_not: string | null;
  trips: number;
  field_h: number;
  logs: number;
  rate_logs_h: number;
  rate_80: [number, number];
  overhead_s: number;
  sightings: number;
  /** hostile players sighted per field hour (the PK part of the death-rate prior) */
  sightings_per_h: number;
  deaths: number;
  /** h_D: deaths (PK or creature) per field hour, posterior mean */
  deaths_per_h: number;
  /** trips a threat ended early without killing us (recall, guard flight, creature stop) */
  sent_home: number;
  /** h_S per field hour */
  sent_home_per_h: number;
  thefts: number;
  /** h_T per field hour */
  thefts_per_h: number;
  /** P(death) in one trip of logs_per_trip */
  p_death_trip: number;
  /** expected logs lost per trip to death and thieves, plus the gear a death loses, in logs */
  loss_logs_trip: number;
  logs_per_trip: number;
  net_logs_h: number;
  supply_gp_trip: number;
  supply_unpriced: number;
  place_fails: number;
  /** the landing rune a trip recalls to (lumber_opt.landing_for), null when none qualifies or no home */
  landing: Landing | null;
  last_trip_h_ago: number | null;
  p_best: number;
  pk_escapes: number;
  creature_recalls: number;     // recalls away from a creature (jobs.recall_cause), not counted in pk_escapes
  creature_hits: number;        // monster_hit damage episodes at the spot
  last_outcome: string | null;
  last_why: string | null;
  /** the landing as text: name (library or own book, tiles from the grove) */
  reach: string | null;
  pvp: boolean;
}

/** A landing rune: a library tome row or a rune of our own book (places.landings) */
export interface Landing {
  source: "library" | "book";
  library: string | null;
  tome: string | null;
  book: string | null;
  name: string;
  x: number;
  y: number;
  /** tiles from the grove's centre */
  dist: number;
  /** planned walk from the landing into the grove (tiles), null: not planned */
  route_tiles: number | null;
  route_checked: boolean;
}

export interface LumberPlan {
  ok: boolean;
  error?: string;
  now: number;
  /** the character's home (harness/data/homes.json); null: none for it (ok false) */
  home: { character: string | null; library: string | null; landing: number[]; at_home: boolean | null } | null;
  skill: number | null;
  success_p: number | null;
  regrow: { minutes: number; pairs: number; regrown?: number; fitted: boolean };
  dispersion: number;
  death_given_sighting: number;
  young: boolean;
  creature_deaths_per_h: number;
  sent_home_pooled_per_h: number;
  thefts_pooled_per_h: number;
  /** share of the carried logs one theft takes (prior 0.5) */
  theft_fraction: number;
  theft_events: number;
  /** what a death loses besides the logs: every unblessed item carried, full price */
  gear_at_risk: {
    gp: number;
    young: boolean;
    items: { item: string; n: number; gp: number }[];
    unpriced: string[];
    source: string | null;
  };
  /** logs we can still carry (weight), null without a live character */
  capacity_logs: number | null;
  prior_rate_logs_h: number;
  prior_cv: number;
  spots: PlanSpot[];
  pick: {
    spot: string;
    mode: "explore" | "exploit";
    greedy: string;
    p_best: number;
    landing: Landing | null;
    logs_per_trip: number;
    trips: number;
    expected_trip_min: number;
    expected_net_logs_h: number;
    /** logs per trip expected in the room's chest (renewal-reward: a death stores nothing) */
    expected_stored_trip: number;
    p_death_trip: number;
    p_sent_home_trip: number;
    command: string;
  } | null;
}

export interface JobAgg {
  trips: number;
  logs: number;
  stored: number;
  /** boards the Resource Stockpile confirmed taking: the trips' plus the overseer's by hand (stored_ctl) */
  stockpiled: number;
  /** boards the overseer put in the stockpile by hand (`ctl act stockpile`; lumber `store` job events) */
  stored_ctl: number;
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

/** One log's price: t null = the woods.json fallback (undated), else the price row's time. */
export interface BoardPrice {
  gp: number;
  t: number | null;
  source: string | null;
}

export interface WoodRow {
  name: string;
  logs: number;
  /** the price now: the newest board:<wood> price, else woods.json */
  value_gp: number | null;
  price_t: number | null;
  price_source: string | null;
  /** the logs at the price each trip used */
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
  nothing_near: number; // Smart Harvest: trees marked out of wood after "nothing nearby" (not attempts)
  yield: number;
  success_rate: number | null;
}

export interface JobsResponse {
  job: string;
  since: number;
  until: number | null;
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
  travel: { legs: LegStats[]; books: BookStats[] };
  supplies: SuppliesTotal;
  time_split: TimeSplit;
  skill: { t: number; skill: number; n: number }[];
}

/** /api/jobs/plan: the lumber_opt plan at the server's clock over all history (null without a store). */
export interface PlanResponse {
  plan: LumberPlan | null;
  store: boolean;
}

/** Plan rows worth a line: active spots and any spot with trips; `hidden` = the untried candidates/disabled. */
export function spotRows(plan: LumberPlan | null): { rows: PlanSpot[]; hidden: number } {
  if (!plan) return { rows: [], hidden: 0 };
  const rows = plan.spots.filter((s) => s.status === "active" || s.trips > 0);
  return { rows, hidden: plan.spots.length - rows.length };
}

/** One travel leg in a few words: "out ✓ charge", "home ✓ 2 casts (disturbed)", "out ✗ walk: no route". */
export function legText(l: TripLeg): { text: string; tone: Tone } {
  const casts = l.tries.length;
  const fails = l.tries.map((t) => t[1]).filter((f): f is string => !!f);
  const how = casts > 1 ? `${casts} casts` : (l.method ?? "");
  const why = l.ok ? (fails.length ? ` (${fails.join(", ")})` : "") : ` ${l.failure ?? fails.join(", ") ?? ""}`;
  return { text: `${l.leg} ${l.ok ? "✓" : "✗"} ${how}${why}`.replace(/\s+/g, " ").trim(), tone: l.ok ? (fails.length ? "warn" : "ok") : "bad" };
}

/** Shares of a time split in display order, for a stacked bar (0 when the total is 0). */
export function splitShares(s: TimeSplit): { key: keyof TimeSplit; s: number; share: number }[] {
  const keys: (keyof TimeSplit)[] = ["travel", "lockout", "field", "other"];
  const total = keys.reduce((a, k) => a + s[k], 0);
  return keys.map((k) => ({ key: k, s: s[k], share: total ? s[k] / total : 0 }));
}

/** "black pearl 2, mandrake root 1" or "—". */
export function fmtCounts(c: Record<string, number> | undefined | null): string {
  const e = Object.entries(c ?? {}).filter(([, n]) => n);
  return e.length ? e.map(([k, n]) => `${k} ${n}`).join(", ") : "—";
}

/** Phases of a trip (since 2026-10-04: home, out, chop, recall home, into the room, convert, store);
 * bank-era rows' to_bank comes after as an extra. */
export const PHASES = ["harvest", "to_room", "convert", "store"] as const;

/** A trip that brought its wood home: "stored" (the room's chest), or the bank era's "banked". */
export function tripHome(outcome: string | null | undefined): boolean {
  return outcome === "stored" || outcome === "banked";
}

/** The outcome badge's kind: home ok; "hopped" (a leg that carried its logs on to another spot by the
 *  home rune library: no abort, the wood comes home with a later leg) info; a place that couldn't be
 *  worked bad; else warn. */
export function outcomeKind(outcome: string | null | undefined, placeFail = false): "ok" | "info" | "bad" | "warn" {
  return tripHome(outcome) ? "ok" : outcome === "hopped" ? "info" : placeFail ? "bad" : "warn";
}

/** Number or an em dash for unknown; `digits` decimals, trailing zeros dropped. */
export function fmtNum(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return String(Number(v.toFixed(digits)));
}

export function fmtGp(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${Math.round(v).toLocaleString("en-US")} gp`;
}

/** "26.8 gp/log (vendor search, 2026-10-05)"; "unpriced" for null. */
export function fmtPrice(p: BoardPrice | null): string {
  if (p === null) return "unpriced";
  const when = p.t === null ? "" : `, ${new Date(p.t * 1000).toLocaleDateString("en-CA")}`;
  return `${p.gp} gp/log (${p.source ?? "?"}${when})`;
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
  /** what the tile counts, shown on hover */
  hint?: string;
  tone: Tone;
}

/** The KPI tile row, in display order. */
export function kpis(t: JobTotals): Kpi[] {
  const hours = fmtHours(t.active_s);
  const boardsPerHour = t.active_s > 0 ? t.stockpiled / (t.active_s / 3600) : null;
  const lost = t.thefts.amount;
  return [
    {
      key: "lph", label: "logs / hr", value: fmtNum(t.logs_per_hour, 0), sub: `${t.logs} logs · ${t.stored} put away`, tone: "info",
      hint: "Logs chopped per active hour (the sum of trip durations; idle time between runs doesn't count). " +
        "Below: logs chopped, and boards put away at home (stockpile, chest or, on old trips, the bank).",
    },
    {
      key: "lpt", label: "logs / trip", value: fmtNum(t.logs_per_trip, 1), sub: t.success_rate === null ? undefined : `${Math.round(t.success_rate * 100)}% chops land`, tone: "info",
      hint: "Mean logs per trip, aborted trips included. Below: the share of chop attempts that gave logs (success / (success + fail)).",
    },
    {
      key: "stored", label: "boards stored", value: fmtInt(t.stockpiled),
      sub: t.stored_ctl ? `in the resource stockpile · ${fmtInt(t.stored_ctl)} by hand` : "in the resource stockpile", tone: "info",
      hint: "Boards the Resource Stockpile confirmed taking (\"You add … to the Resource Stockpile.\"): the trips' and the ones the " +
        "overseer put there by hand (an aborted trip's boards, `ctl act stockpile`). Boards put in the chest don't count.",
    },
    {
      key: "bph", label: "boards / hr", value: fmtNum(boardsPerHour, 0), sub: "per active hour", tone: "info",
      hint: "Boards the Resource Stockpile confirmed taking, per active hour (the sum of trip durations; idle time between runs doesn't count).",
    },
    { key: "trips", label: "trips", value: String(t.trips), tone: "dim", hint: "Trips that started in the date range, aborted ones included." },
    { key: "active", label: "active hours", value: hours.value, sub: hours.sub, tone: "dim", hint: "The sum of trip durations, start to end." },
    {
      key: "pk", label: "deaths to PKs", value: String(t.deaths.pk), sub: t.pk_seen ? `${t.pk_seen} PK sighting${t.pk_seen === 1 ? "" : "s"}` : undefined, tone: t.deaths.pk ? "bad" : "ok",
      hint: "Deaths recorded with a player as the cause. Below: hostile players (red, grey, attackers) seen during trips.",
    },
    {
      key: "mob", label: "deaths to mobs", value: String(t.deaths.mob), sub: t.deaths.other ? `+${t.deaths.other} other` : undefined, tone: t.deaths.mob ? "bad" : "ok",
      hint: "Deaths recorded with a creature as the cause. \"Other\": deaths with no known cause.",
    },
    {
      key: "theft", label: "loss to thieves", value: String(lost), sub: `${t.thefts.count} theft${t.thefts.count === 1 ? "" : "s"}`, tone: lost || t.thefts.count ? "warn" : "ok",
      hint: "Logs, boards and items lost to suspected thefts: the pack count dropped with no action of ours.",
    },
  ];
}

export interface EventView {
  label: string;
  detail: string;
  tone: Tone;
}

/** What each job event kind means, for hover text in the event list and on the strip. */
export const EVENT_HELP: Record<string, string> = {
  death: "We died. The cause is pk (a player), mob (a creature) or unknown.",
  theft: "A suspected theft: wood or items left the pack with no action of ours.",
  pk_seen: "A hostile player (red, grey, or attacking us) came into view.",
  flee: "We walked or ran away from a threat and carried on.",
  recall: "A recall away from a threat (an escape leg).",
  guard_flight: "We ran into a guarded town to escape a threat.",
  speech_hold: "Someone spoke nearby, so the runner paused for the human or the overseer.",
  speech_clear: "The speech hold ended and work resumed.",
  stand:
    "One harvest (stored as a `stand` event): a tile the runner stood on and chopped from with Smart Harvest (it targets itself and " +
    "the server picks a tree in reach). Records the trees in reach, chops, logs and why it moved on (out of wood, quota, break…).",
  monster_seen: "A hostile creature the runner steered clear of. These teach which bodies are aggressive and which areas to avoid.",
  monster_hit: "A creature damaged us. Records who, how much and what the runner did (run, walk on, recall or stop).",
  tracking: "A Tracking skill use to check for murderers nearby.",
  thief:
    "The thief guard reacted: our trapped pouch went off in someone else's hands, or a suspect was walked away from. " +
    "One that made us leave keeps the spot off the plan for 20 min.",
  aspect: "The Harvest Aspect was activated before chopping.",
  resurrect: "We were resurrected.",
  mob_attack: "A creature attacked us.",
  leave: "The hunt runner left the spot (low hits, done, …).",
};

/** Short display names of event kinds where the stored kind isn't clear (`stand` is a harvest). */
const EVENT_NAME: Record<string, string> = {
  death: "died",
  pk_seen: "PK seen",
  flee: "fled",
  leave: "left",
  speech_hold: "paused (speech)",
  speech_clear: "resumed",
  stand: "harvest",
};

/** An event kind as the dashboards show it: its short name, else the kind with spaces. */
export function eventName(kind: string): string {
  return EVENT_NAME[kind] ?? kind.replace(/_/g, " ");
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
    case "leave":
      return { label: "Left the hunt", detail: [str(d.why), detail].filter(Boolean).join(" · "), tone: "info" };
    case "speech_hold": {
      const sp = Array.isArray(d.speakers) ? (d.speakers[0] as Record<string, unknown> | undefined) : undefined;
      const name = sp ? (str(sp.label) ?? str(sp.name)) : null;
      const said = sp && str(sp.text) ? `“${String(sp.text)}”` : null;
      return { label: "Paused: someone spoke", detail: [name, said, at].filter(Boolean).join(" · "), tone: "warn" };
    }
    case "speech_clear":
      return { label: "Resumed", detail: typeof d.waited_s === "number" ? `after ${fmtDuration(d.waited_s)}` : detail, tone: "ok" };
    case "travel":
    case "recall": {
      const leg = str(d.leg) ?? "escape";
      const where = str(d.name) ?? (d.witcher_rune ? `rune ${String(d.witcher_rune)}` : null);
      const how = [str(d.method), typeof d.charges === "number" ? `${d.charges} charges` : null].filter(Boolean).join(", ");
      return {
        label: `Recall ${leg} ${d.ok ? "landed" : "failed"}`,
        detail: [where, how, d.ok ? null : str(d.failure), at].filter(Boolean).join(" · "),
        tone: d.ok ? (leg === "escape" ? "warn" : "info") : "bad",
      };
    }
    case "guard_flight":
      return { label: "Fled into the guards", detail, tone: "warn" };
    default:
      return { label: eventName(e.kind), detail, tone: "dim" };
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
  /** the price now, null when the wood has none */
  price: BoardPrice | null;
  total_gp: number | null;
}

/** Woods with logs > 0, most logs first, with their share of all logs. */
export function woodShares(rows: readonly WoodRow[]): WoodShare[] {
  const withLogs = rows.filter((r) => r.logs > 0);
  const sum = withLogs.reduce((a, r) => a + r.logs, 0);
  return withLogs
    .map((r) => ({
      name: r.name,
      logs: r.logs,
      share: sum ? r.logs / sum : 0,
      price: r.value_gp === null ? null : { gp: r.value_gp, t: r.price_t, source: r.price_source },
      total_gp: r.total_gp,
    }))
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

// ------------------------------------------------------------------ date range
/** The Jobs page's date range: local calendar days "YYYY-MM-DD", both inclusive; null = open-ended. */
export interface DateRange {
  from: string | null;
  to: string | null;
}

/** [since, until) in epoch seconds for /api/jobs; null = unbounded. */
export interface RangeBounds {
  since: number | null;
  until: number | null;
}

const DAY_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

function dayDate(day: string, plus = 0): Date | null {
  const m = DAY_RE.exec(day);
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]) - 1, Number(m[3])];
  const probe = new Date(y, mo, d);
  if (probe.getFullYear() !== y || probe.getMonth() !== mo || probe.getDate() !== d) return null; // 2026-02-30
  return new Date(y, mo, d + plus);
}

/** Local "YYYY-MM-DD" of epoch seconds `t`. */
export function localDay(t: number): string {
  const d = new Date(t * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** `day` moved by `n` calendar days (local, so DST days stay whole), or null for a bad day. */
export function addDays(day: string, n: number): string | null {
  const d = dayDate(day, n);
  return d ? localDay(d.getTime() / 1000) : null;
}

/** since = local midnight starting `from`; until = local midnight after `to` (exclusive). */
export function rangeBounds(r: DateRange): RangeBounds {
  const a = r.from ? dayDate(r.from) : null;
  const b = r.to ? dayDate(r.to, 1) : null;
  return { since: a ? a.getTime() / 1000 : null, until: b ? b.getTime() / 1000 : null };
}

/** The range in a URL query ("from=YYYY-MM-DD&to=YYYY-MM-DD", either optional): bad
 *  dates are dropped, a reversed pair swapped. */
export function parseRange(query: string): DateRange {
  const q = new URLSearchParams(query);
  const [from = null, to = null] = [q.get("from"), q.get("to")].map((v) => (v !== null && dayDate(v) ? v : null));
  return from && to && from > to ? { from: to, to: from } : { from, to };
}

/** The canonical query of a range ("" for all time); parseRange's inverse. */
export function rangeQuery(r: DateRange): string {
  const parts: string[] = [];
  if (r.from) parts.push(`from=${r.from}`);
  if (r.to) parts.push(`to=${r.to}`);
  return parts.join("&");
}

export const RANGE_PRESETS = [
  { key: "all", label: "All" },
  { key: "today", label: "Today" },
  { key: "7d", label: "7 days" },
  { key: "30d", label: "30 days" },
] as const;
export type RangePreset = (typeof RANGE_PRESETS)[number]["key"];

/** A preset as days, open-ended so new trips keep showing: the last N days including `today`. */
export function presetRange(key: RangePreset, today: string): DateRange {
  const back = { all: null, today: 0, "7d": 6, "30d": 29 }[key];
  return back === null ? { from: null, to: null } : { from: addDays(today, -back), to: null };
}

// ------------------------------------------------------------------ hunt job
// /api/jobs?job=hunt (harness/jobs.py compute_hunt). XP is Outlands mastery-chain
// experience: a kill's creature gold value x our damage share, estimated as the gold
// its corpse held (docs/HUNT_LOOP.md "Memory").

export interface HuntVisit {
  /** 1-based over all visits in the answer (visit is the runner's own per-run number). */
  n: number;
  visit: number | null;
  t_start: number | null;
  t_end: number | null;
  duration_s: number | null;
  spot: number[] | null;
  spell: string | null;
  ended: string | null;
  kills: number;
  gold: number;
  xp: number;
  /** looted kills whose XP is known */
  xp_kills: number;
  looted: number;
  hits_lost: number;
  casts: number;
  heals: number;
  potions: number;
  kills_per_hour: number | null;
  gold_per_hour: number | null;
  xp_per_hour: number | null;
  /** other job_events kinds inside the visit, counted */
  events: Record<string, number>;
}

export interface HuntAgg {
  visits: number;
  active_s: number;
  active_hours: number;
  kills: number;
  gold: number;
  xp: number;
  xp_kills: number;
  xp_unknown_kills: number;
  looted: number;
  gold_per_kill: number | null;
  xp_per_kill: number | null;
  hits_lost: number;
  casts: number;
  heals: number;
  potions: number;
  leaves: number;
  speech_holds: number;
  speech_wait_s: number;
  deaths: { pk: number; mob: number; other: number; total: number };
}

export interface HuntTotals extends HuntAgg {
  kills_per_hour: number | null;
  gold_per_hour: number | null;
  xp_per_hour: number | null;
  /** kill/loot events no visit row covers (a run that ended without its row) */
  outside_visits: { kills: number; gold: number; xp: number };
  first_t: number | null;
  last_t: number | null;
}

export interface HuntDay extends HuntAgg {
  day: string;
}

export interface HuntRollingPoint {
  t: number;
  n: number;
  window_visits: number;
  kills_per_hour: number | null;
  cum_kills_per_hour: number | null;
  gold_per_hour: number | null;
  cum_gold_per_hour: number | null;
  xp_per_hour: number | null;
  cum_xp_per_hour: number | null;
}

export interface MonsterRow {
  name: string;
  kills: number;
  looted: number;
  gold: number;
  xp: number;
  xp_kills: number;
  gold_per_kill: number | null;
  xp_per_kill: number | null;
}

export interface HuntResponse {
  job: "hunt";
  since: number;
  until: number | null;
  utc_offset_s: number;
  window_s: number;
  store: boolean;
  visits: HuntVisit[];
  totals: HuntTotals;
  days: HuntDay[];
  rolling: HuntRollingPoint[];
  monsters: MonsterRow[];
  /** job events except the per-kill ones (kill, loot) */
  events: JobEvent[];
}

/** Integer with thousands separators, or an em dash. */
export function fmtInt(v: number | null | undefined): string {
  return v === null || v === undefined || !Number.isFinite(v) ? "—" : Math.round(v).toLocaleString("en-US");
}

function perHour(v: number | null, unit = ""): string {
  return `${fmtInt(v)}${unit} / hr`;
}

/** The hunt KPI tile row, in display order. */
export function huntKpis(t: HuntTotals): Kpi[] {
  const hours = fmtHours(t.active_s);
  const unknown = t.xp_unknown_kills;
  return [
    { key: "kills", label: "mobs killed", value: fmtInt(t.kills), sub: perHour(t.kills_per_hour), tone: "info" },
    {
      key: "gold",
      label: "gold looted",
      value: fmtGp(t.gold),
      sub: `${perHour(t.gold_per_hour, " gp")}${t.gold_per_kill === null ? "" : ` · ${fmtNum(t.gold_per_kill, 1)} / kill`}`,
      tone: "info",
    },
    {
      key: "xp",
      label: "XP earned (est.)",
      value: fmtInt(t.xp),
      sub: `${perHour(t.xp_per_hour)}${unknown ? ` · ${unknown} kill${unknown === 1 ? "" : "s"} not looted` : ""}`,
      tone: "info",
    },
    { key: "visits", label: "visits", value: String(t.visits), sub: t.leaves ? `${t.leaves} leave${t.leaves === 1 ? "" : "s"}` : undefined, tone: "dim" },
    { key: "active", label: "active hours", value: hours.value, sub: hours.sub, tone: "dim" },
    { key: "mob", label: "deaths to mobs", value: String(t.deaths.mob), sub: t.deaths.other ? `+${t.deaths.other} other` : undefined, tone: t.deaths.mob ? "bad" : "ok" },
    { key: "pk", label: "deaths to PKs", value: String(t.deaths.pk), tone: t.deaths.pk ? "bad" : "ok" },
    { key: "hits", label: "hits lost", value: fmtInt(t.hits_lost), sub: `${t.heals} heal${t.heals === 1 ? "" : "s"} · ${t.potions} potion${t.potions === 1 ? "" : "s"}`, tone: "dim" },
    {
      key: "speech",
      label: "speech holds",
      value: String(t.speech_holds),
      sub: t.speech_holds ? `${fmtDuration(t.speech_wait_s)} waiting` : undefined,
      tone: t.speech_holds ? "warn" : "ok",
    },
  ];
}

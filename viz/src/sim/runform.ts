// The Sim page's job launcher (components/SimRunPanel.tsx): its form state, the /api/sim/jobs request
// built from it (validated, numbers clamped to the server's limits; option rows checked against the
// parsers' schemas the server sends) and what changed between job polls.
import type { SimArgSpec, SimFight, SimJob, SimJobRequest, SimJobStatus, SimRunLimits, SimRunOptions } from "./types.ts";

export type Result<T> = { ok: true; value: T } | { ok: false; error: string };

/** One chosen option of run.py's or the runner's: its flag, its values as typed (space-separated when it
 *  takes several; one value may hold spaces), and for an on/off option whether it is the `--no-` form. */
export interface ArgRow {
  flag: string;
  value: string;
  off: boolean;
}

/** One `--set module.NAME=value`. */
export interface SetRow {
  name: string;
  value: string;
}

/** run.py's form: the numbers as typed (checked and clamped when the request is built), the options as rows. */
export interface RunForm {
  scenario: string;
  /** "" = the scenario's own level. */
  level: string;
  seed: string;
  runs: string;
  minutes: string;
  careers: string;
  at_hunt: boolean;
  others: boolean;
  eerie: boolean;
  reds: boolean;
  prior: boolean;
  fresh: boolean;
  trace: boolean;
  sets: SetRow[];
  run_args: ArgRow[];
  runner_args: ArgRow[];
  /** A team preset name, "" = the scenario's team. */
  team: string;
  /** A Taming Bestiary loadout, "" = the team's own. */
  bestiary: string;
}

export type NumField = keyof SimRunLimits;

/** Whole numbers, except minutes. */
export const isIntField = (k: NumField) => k !== "minutes";

/** argv words as rows: a `--flag` starts one (an on/off option's `--no-` form its off row), the words
 *  after it are its value. */
export function rowsFromWords(words: readonly string[], specs: readonly SimArgSpec[]): ArgRow[] {
  const rows: ArgRow[] = [];
  for (const w of words) {
    if (w.startsWith("--")) {
      const hit = specOf(specs, w);
      rows.push({ flag: hit?.spec.flag ?? w, value: "", off: hit?.off ?? false });
    } else if (rows.length) {
      const r = rows[rows.length - 1]!;
      r.value = r.value ? `${r.value} ${w}` : w;
    }
  }
  return rows;
}

/** The option schemas a form's rows follow (SimOptions.run_args, SimOptions.runner_args). */
export interface ArgSpecs {
  runSpecs: readonly SimArgSpec[];
  runnerSpecs: readonly SimArgSpec[];
}

export function runFormFrom(d: SimRunOptions, specs: ArgSpecs): RunForm {
  return {
    ...d,
    seed: String(d.seed),
    runs: String(d.runs),
    minutes: String(d.minutes),
    careers: String(d.careers),
    sets: d.sets.map(setRowOf),
    run_args: rowsFromWords(d.run_args, specs.runSpecs),
    runner_args: rowsFromWords(d.runner_args, specs.runnerSpecs),
  };
}

/** docs/SIM.md's parity command's runner args. */
export const PARITY_RUNNER_ARGS: readonly ArgRow[] = [
  { flag: "--vet-range", value: "5", off: false },
  { flag: "--crawl-learn-s", value: "180", off: false },
];

/** docs/SIM.md's parity batch: 144 runs from seed 17 with other players, eerie corpses and the live
 *  prior (when there is one), the parity runner args, no traces; the rest at run.py's defaults. */
export function parityForm(d: SimRunOptions, priorAvailable: boolean, specs: ArgSpecs): RunForm {
  return {
    ...runFormFrom(d, specs),
    runs: "144",
    seed: "17",
    others: true,
    eerie: true,
    prior: priorAvailable,
    runner_args: PARITY_RUNNER_ARGS.map((r) => ({ ...r })),
    trace: false,
  };
}

/** `text` as a number clamped to `[lo, hi]` (rounded when `int`), or null when it is not a number. */
export function clampField(text: string, [lo, hi]: readonly [number, number], int: boolean): number | null {
  const s = text.trim();
  if (!s) return null;
  const n = Number(s);
  if (!Number.isFinite(n)) return null;
  return Math.min(hi, Math.max(lo, int ? Math.round(n) : n));
}

/** `module.NAME` (the module may be dotted). */
const SET_NAME_RE = /^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$/;

/** `module.NAME=value` as a row (the name trimmed; no "=" gives an empty value). */
export function setRowOf(line: string): SetRow {
  const i = line.indexOf("=");
  return i < 0 ? { name: line.trim(), value: "" } : { name: line.slice(0, i).trim(), value: line.slice(i + 1).trim() };
}

/** Shell-style words: whitespace splits, '…' and "…" quote; an unclosed quote is an error. */
export function splitWords(text: string): Result<string[]> {
  const out: string[] = [];
  let cur = "";
  let has = false;
  let quote: string | null = null;
  for (const ch of text) {
    if (quote) {
      if (ch === quote) quote = null;
      else cur += ch;
    } else if (ch === '"' || ch === "'") {
      quote = ch;
      has = true;
    } else if (/\s/.test(ch)) {
      if (has) out.push(cur);
      cur = "";
      has = false;
    } else {
      cur += ch;
      has = true;
    }
  }
  if (quote) return { ok: false, error: `unclosed ${quote}` };
  if (has) out.push(cur);
  return { ok: true, value: out };
}

/** The spec a flag names (its on or its `--no-` form) and whether it is the off form. */
export function specOf(specs: readonly SimArgSpec[], flag: string): { spec: SimArgSpec; off: boolean } | null {
  for (const s of specs) {
    if (s.flag === flag) return { spec: s, off: false };
    if (s.kind === "bool" && s.off === flag) return { spec: s, off: true };
  }
  return null;
}

/** What a value option takes, e.g. "2 values (X Y)". */
export function takes(spec: Extract<SimArgSpec, { kind: "value" }>): string {
  const mv = spec.metavar.join(" ");
  if (spec.nargs === "?") return `an optional value (${mv})`;
  if (spec.nargs === "*") return `any number of values (${mv} …)`;
  if (spec.nargs === "+") return `one or more values (${mv} …)`;
  return spec.nargs === 1 ? `a value (${mv})` : `${spec.nargs} values (${mv})`;
}

/** A new row for `spec`: its default as the value; an on/off option starts on the form that changes it. */
export function newRow(spec: SimArgSpec): ArgRow {
  if (spec.kind === "bool") return { flag: spec.flag, value: "", off: spec.default === true };
  if (spec.kind === "flag") return { flag: spec.flag, value: "", off: false };
  const d = spec.default;
  const value = d === null || d === undefined || (Array.isArray(d) && !d.length) ? "" : Array.isArray(d) ? d.join(" ") : String(d);
  return { flag: spec.flag, value, off: false };
}

/** The rows as argv words, or the first row that doesn't fit its option (unknown, values miscounted, not a
 *  choice, given twice when it doesn't repeat). */
export function argWords(rows: readonly ArgRow[], specs: readonly SimArgSpec[]): Result<string[]> {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const r of rows) {
    const spec = specs.find((s) => s.flag === r.flag);
    if (!spec) return { ok: false, error: `${r.flag}: not an option here` };
    if (seen.has(spec.flag) && !spec.repeat) return { ok: false, error: `${r.flag}: given twice (it doesn't repeat)` };
    seen.add(spec.flag);
    if (spec.kind === "flag") {
      out.push(spec.flag);
      continue;
    }
    if (spec.kind === "bool") {
      out.push(r.off ? spec.off : spec.flag);
      continue;
    }
    let vals: string[];
    if (spec.nargs === 1 || spec.nargs === "?") {
      const v = r.value.trim();
      vals = v ? [v] : [];
    } else {
      const w = splitWords(r.value);
      if (!w.ok) return { ok: false, error: `${r.flag}: ${w.error}` };
      vals = w.value;
    }
    const n = vals.length;
    const fits = spec.nargs === "?" ? n <= 1 : spec.nargs === "*" ? true : spec.nargs === "+" ? n >= 1 : n === spec.nargs;
    if (!fits) return { ok: false, error: `${r.flag}: takes ${takes(spec)}` };
    if (spec.choices) {
      const bad = vals.find((v) => !spec.choices!.includes(v));
      if (bad !== undefined) return { ok: false, error: `${r.flag}: "${bad}" is not one of ${spec.choices.join(", ")}` };
    }
    out.push(spec.flag, ...vals);
  }
  return { ok: true, value: out };
}

/** Pasted arguments (a command's tail, `--` and all) as rows; an unknown flag or a value miscounted is an error. */
export function parseArgs(text: string, specs: readonly SimArgSpec[]): Result<ArgRow[]> {
  const w = splitWords(text);
  if (!w.ok) return w;
  const words = w.value.filter((x) => x !== "--");
  const rows: ArgRow[] = [];
  let i = 0;
  while (i < words.length) {
    const word = words[i++]!;
    const eq = word.startsWith("--") ? word.indexOf("=") : -1;
    const flag = eq > 0 ? word.slice(0, eq) : word;
    const hit = specOf(specs, flag);
    if (!hit) return { ok: false, error: word.startsWith("--") ? `${flag}: not an option here` : `"${word}": a value without its option` };
    const { spec, off } = hit;
    if (spec.kind !== "value") {
      rows.push({ flag: spec.flag, value: "", off });
      continue;
    }
    const vals: string[] = eq > 0 ? [word.slice(eq + 1)] : [];
    const want = typeof spec.nargs === "number" ? spec.nargs : spec.nargs === "?" ? 1 : Infinity;
    while (vals.length < want && i < words.length && !words[i]!.startsWith("--")) vals.push(words[i++]!);
    if (typeof spec.nargs === "number" && vals.length !== spec.nargs) return { ok: false, error: `${flag}: takes ${takes(spec)}` };
    if (spec.nargs === "+" && !vals.length) return { ok: false, error: `${flag}: takes ${takes(spec)}` };
    const single = spec.nargs === 1 || spec.nargs === "?";
    const quoted = single ? vals : vals.map((v) => (!/\s/.test(v) ? v : v.includes('"') ? `'${v}'` : `"${v}"`));
    rows.push({ flag: spec.flag, value: quoted.join(" "), off: false });
  }
  return { ok: true, value: rows };
}

/** The limits and specs the request is checked against. */
export interface RunContext extends ArgSpecs {
  limits: SimRunLimits;
  priorAvailable: boolean;
}

/** The run request, or the first thing wrong with the form. Prior is off when the store has no history. */
export function runRequest(f: RunForm, ctx: RunContext): Result<SimJobRequest> {
  const { limits } = ctx;
  let bad: string | null = null;
  const num = (k: NumField): number => {
    const v = clampField(f[k], limits[k], isIntField(k));
    if (v === null) bad ??= `${k}: "${f[k]}" is not a number`;
    return v ?? 0;
  };
  const seed = num("seed");
  const runs = num("runs");
  const minutes = num("minutes");
  const careers = num("careers");
  if (bad !== null) return { ok: false, error: bad };
  const sets: string[] = [];
  for (const r of f.sets) {
    if (!SET_NAME_RE.test(r.name)) return { ok: false, error: `--set "${r.name}": expected module.NAME` };
    if (!r.value.trim()) return { ok: false, error: `--set ${r.name}: no value` };
    sets.push(`${r.name}=${r.value.trim()}`);
  }
  const runArgs = argWords(f.run_args, ctx.runSpecs);
  if (!runArgs.ok) return { ok: false, error: `run.py ${runArgs.error}` };
  const runnerArgs = argWords(f.runner_args, ctx.runnerSpecs);
  if (!runnerArgs.ok) return { ok: false, error: `runner ${runnerArgs.error}` };
  const options: SimRunOptions = {
    scenario: f.scenario,
    level: f.level,
    seed,
    runs,
    minutes,
    careers,
    at_hunt: f.at_hunt,
    others: f.others,
    eerie: f.eerie,
    reds: f.reds,
    prior: f.prior && ctx.priorAvailable,
    fresh: f.fresh,
    trace: f.trace,
    sets,
    run_args: runArgs.value,
    runner_args: runnerArgs.value,
    team: f.team,
    bestiary: f.bestiary,
  };
  return { ok: true, value: { kind: "run", options } };
}

/** A fight script's form: its flags by arg, its choices by option index ("" = none). */
export interface FightForm {
  fight: string;
  seeds: string;
  flags: Record<string, boolean>;
  choices: Record<number, string>;
  trace: boolean;
}

export function fightFormFrom(fight: SimFight | undefined, trace: boolean, seeds = "1"): FightForm {
  return { fight: fight?.name ?? "", seeds, flags: {}, choices: {}, trace };
}

/** How many seeds a fight script's seed list (`1,2,5-8`, spaces ignored) names, or what is wrong with it. */
export function countSeeds(text: string): Result<number> {
  const s = text.replace(/\s+/g, "");
  if (!s) return { ok: false, error: "seeds: enter a list like 1,2,5-8" };
  let n = 0;
  for (const part of s.split(",")) {
    const m = /^(\d+)(?:-(\d+))?$/.exec(part);
    if (!m) return { ok: false, error: `seeds: "${part}" is not a seed or a range a-b` };
    const a = Number(m[1]);
    const b = m[2] === undefined ? a : Number(m[2]);
    if (b < a) return { ok: false, error: `seeds: ${part} runs backwards` };
    n += b - a + 1;
  }
  return { ok: true, value: n };
}

/** The chosen flags and choice values, in the script's option order. */
export function fightArgs(fight: SimFight, f: FightForm): string[] {
  const args: string[] = [];
  fight.options.forEach((o, i) => {
    if (o.type === "flag") {
      if (f.flags[o.arg]) args.push(o.arg);
    } else {
      const v = f.choices[i];
      if (v && o.args.includes(v)) args.push(v);
    }
  });
  return args;
}

/** The fight request, or the first thing wrong with the form. */
export function fightRequest(fights: readonly SimFight[], f: FightForm): Result<SimJobRequest> {
  const fight = fights.find((x) => x.name === f.fight);
  if (!fight) return { ok: false, error: "pick a fight script" };
  const n = countSeeds(f.seeds);
  if (!n.ok) return n;
  return { ok: true, value: { kind: "fight", fight: fight.name, seeds: f.seeds.replace(/\s+/g, ""), args: fightArgs(fight, f), trace: f.trace } };
}

/** Above this many traced runs the launcher warns (one trace file per run). */
export const MANY_TRACES = 8;

/** Per job id: the status and trace count last seen. */
export type JobsSeen = ReadonlyMap<number, { status: SimJobStatus; traces: number }>;

/** What changed since the last poll: jobs that ended (were running, or are new and already over) and
 *  whether any job has new trace files. The first poll (`seen` null) only records. */
export function diffJobs(seen: JobsSeen | null, jobs: readonly SimJob[]): { ended: SimJob[]; newTraces: boolean; seen: JobsSeen } {
  const next: JobsSeen = new Map(jobs.map((j) => [j.id, { status: j.status, traces: j.traces.length }]));
  if (!seen) return { ended: [], newTraces: false, seen: next };
  const ended = jobs.filter((j) => j.status !== "running" && (seen.get(j.id)?.status ?? "running") === "running");
  const newTraces = jobs.some((j) => j.traces.length > (seen.get(j.id)?.traces ?? 0));
  return { ended, newTraces, seen: next };
}

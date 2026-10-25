// The Sim page's job launcher (components/SimRunPanel.tsx): its form state, the /api/sim/jobs request
// built from it (validated, numbers clamped to the server's limits) and what changed between job polls.
import type { SimFight, SimJob, SimJobRequest, SimJobStatus, SimRunLimits, SimRunOptions } from "./types.ts";

export type Result<T> = { ok: true; value: T } | { ok: false; error: string };

/** run.py's form: the numbers as typed (checked and clamped when the request is built), `--set` lines as text. */
export interface RunForm {
  scenario: string;
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
  /** One `module.NAME=value` per line. */
  sets: string;
  runner_args: string;
  /** A team preset name, "" = the scenario's team. */
  team: string;
  /** A Taming Bestiary loadout, "" = the team's own. */
  bestiary: string;
}

export type NumField = keyof SimRunLimits;

/** Whole numbers, except minutes. */
export const isIntField = (k: NumField) => k !== "minutes";

export function runFormFrom(d: SimRunOptions): RunForm {
  return {
    ...d,
    seed: String(d.seed),
    runs: String(d.runs),
    minutes: String(d.minutes),
    careers: String(d.careers),
    sets: d.sets.join("\n"),
  };
}

/** docs/SIM.md's parity command's runner args. */
export const PARITY_RUNNER_ARGS = "--vet-range 5 --crawl-learn-s 180";

/** docs/SIM.md's parity batch: 144 runs from seed 17 with other players, eerie corpses and the live
 *  prior (when there is one), the parity runner args, no traces; the rest at run.py's defaults. */
export function parityForm(d: SimRunOptions, priorAvailable: boolean): RunForm {
  return {
    ...runFormFrom(d),
    runs: "144",
    seed: "17",
    others: true,
    eerie: true,
    prior: priorAvailable,
    runner_args: PARITY_RUNNER_ARGS,
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

/** `module.NAME=value` (the module may be dotted; the value may be empty). */
const SET_RE = /^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+=/;

/** The run request, or the first thing wrong with the form. Prior is off when the store has no history. */
export function runRequest(f: RunForm, limits: SimRunLimits, priorAvailable: boolean): Result<SimJobRequest> {
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
  for (const [i, raw] of f.sets.split("\n").entries()) {
    const line = raw.trim();
    if (!line) continue;
    if (!SET_RE.test(line)) return { ok: false, error: `--set line ${i + 1} "${line}": expected module.NAME=value` };
    sets.push(line);
  }
  const options: SimRunOptions = {
    scenario: f.scenario,
    seed,
    runs,
    minutes,
    careers,
    at_hunt: f.at_hunt,
    others: f.others,
    eerie: f.eerie,
    reds: f.reds,
    prior: f.prior && priorAvailable,
    fresh: f.fresh,
    trace: f.trace,
    sets,
    runner_args: f.runner_args.trim(),
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

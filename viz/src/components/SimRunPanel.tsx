// The Sim page's job launcher (docs/VISUALIZER.md §2.20): start harness/sim/run.py or a fight*.py with
// chosen options through viz_server's /api/sim/jobs (one job at a time), follow its output, and load
// the traces it writes. A drawer over the page's right side; useSimJobs polls on the page's behalf so
// a job's end refreshes the trace list (and loads its one trace) with the drawer closed too.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { cancelSimJob, fetchSimOptions, listSimJobs, startSimJob } from "../sim/api.ts";
import {
  MANY_TRACES,
  PARITY_RUNNER_ARGS,
  clampField,
  countSeeds,
  diffJobs,
  fightFormFrom,
  fightRequest,
  isIntField,
  parityForm,
  runFormFrom,
  runRequest,
  type FightForm,
  type JobsSeen,
  type NumField,
  type RunForm,
} from "../sim/runform.ts";
import type { SimFight, SimJob, SimJobRequest, SimJobStatus, SimOptions } from "../sim/types.ts";
import { Badge, Panel } from "./common.tsx";

const FAST_MS = 1000;
const SLOW_MS = 10_000;

export interface SimJobs {
  /** Newest first; null until the first answer. */
  jobs: SimJob[] | null;
  error: string | null;
  running: SimJob | null;
  /** Start a job (throws with the server's error); its end is reported with `mine` true. */
  start: (req: SimJobRequest) => Promise<void>;
  cancel: (id: number) => Promise<void>;
}

export interface SimJobEvents {
  /** A job ended; `mine` when it was started from this page. */
  ended: (job: SimJob, mine: boolean) => void;
  /** A job ended or wrote new trace files. */
  traces: () => void;
}

/** Polls /api/sim/jobs every second while a job runs or the drawer is `open`, else every 10 s. */
export function useSimJobs(open: boolean, on: SimJobEvents): SimJobs {
  const [jobs, setJobs] = useState<SimJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kick, setKick] = useState(0);
  const seen = useRef<JobsSeen | null>(null);
  const mine = useRef(new Set<number>());
  const onRef = useRef(on);
  onRef.current = on;
  const running = jobs?.find((j) => j.status === "running") ?? null;
  const fast = open || running !== null;

  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    const poll = () => {
      listSimJobs()
        .then(
          (js) => {
            if (!live) return;
            const d = diffJobs(seen.current, js);
            seen.current = d.seen;
            setJobs(js);
            setError(null);
            for (const j of d.ended) onRef.current.ended(j, mine.current.has(j.id));
            if (d.ended.length || d.newTraces) onRef.current.traces();
          },
          (e: unknown) => {
            if (live) setError(String(e));
          },
        )
        .finally(() => {
          if (live) timer = window.setTimeout(poll, fast ? FAST_MS : SLOW_MS);
        });
    };
    poll();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [fast, kick]);

  const start = useCallback(async (req: SimJobRequest) => {
    const job = await startSimJob(req);
    mine.current.add(job.id);
    setJobs((js) => [job, ...(js ?? []).filter((j) => j.id !== job.id)]);
    setKick((k) => k + 1);
  }, []);
  const cancel = useCallback(async (id: number) => {
    await cancelSimJob(id);
    setKick((k) => k + 1);
  }, []);
  return { jobs, error, running, start, cancel };
}

const STATUS_BADGE: Record<SimJobStatus, "info" | "ok" | "bad" | "warn"> = { running: "info", done: "ok", failed: "bad", cancelled: "warn" };

/** 42s, 3:07, 1:02:03. */
function fmtDuration(s: number): string {
  const n = Math.max(0, Math.round(s));
  if (n < 60) return `${n}s`;
  const h = Math.floor(n / 3600);
  const m = Math.floor((n % 3600) / 60);
  const ss = String(n % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

type Update<T> = (fn: (f: T) => T) => void;
type RunFlag = "at_hunt" | "others" | "eerie" | "reds" | "prior" | "fresh" | "trace";

function Check({ checked, onChange, label, title, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; title: string; disabled?: boolean }) {
  return (
    <label className={disabled ? "sim-check disabled" : "sim-check"} title={title}>
      <input type="checkbox" checked={checked && !disabled} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

function RunFields({ form, upd, opts }: { form: RunForm; upd: Update<RunForm>; opts: SimOptions }) {
  const { limits } = opts.run;
  const num = (k: NumField, label: string, step: number, title: string) => (
    <label className="sim-field" title={`${title} (${limits[k][0]}–${limits[k][1]})`}>
      <span>{label}</span>
      <input
        type="number"
        min={limits[k][0]}
        max={limits[k][1]}
        step={step}
        value={form[k]}
        onChange={(e) => {
          const v = e.target.value;
          upd((f) => ({ ...f, [k]: v }));
        }}
        onBlur={() =>
          upd((f) => {
            const v = clampField(f[k], limits[k], isIntField(k));
            return v === null ? f : { ...f, [k]: String(v) };
          })
        }
      />
    </label>
  );
  const flag = (k: RunFlag, label: string, title: string, disabled?: boolean) => (
    <Check checked={form[k]} onChange={(v) => upd((f) => ({ ...f, [k]: v }))} label={label} title={title} disabled={disabled} />
  );
  const scenarios = opts.scenarios.includes(form.scenario) ? opts.scenarios : [form.scenario, ...opts.scenarios];
  const runs = Number(form.runs);
  const prior = opts.prior;
  return (
    <>
      <div className="sim-form-row">
        <label className="sim-field">
          <span>scenario</span>
          <select
            value={form.scenario}
            onChange={(e) => {
              const v = e.target.value;
              upd((f) => ({ ...f, scenario: v }));
            }}
          >
            {scenarios.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        {num("seed", "seed", 1, "--seed: the first run's seed; run i uses seed + i")}
        {num("runs", "runs", 1, "--runs")}
        {num("minutes", "minutes", 0.5, "--minutes: the runner's timeout, in game minutes")}
        {num("careers", "careers", 1, "--careers: split the runs into this many careers on one memory store each; 0 = run.py's default (CPU count - 1)")}
      </div>
      <div className="sim-form-row">
        {flag("at_hunt", "at hunt", "--at-hunt: start on the hunting level (no recall in)")}
        {flag("others", "other players", "--others: other players on the level")}
        {flag("eerie", "eerie corpses", "--eerie: the Field of Souls' eerie corpses on the level, empty soul jars in the pack")}
        {flag("reds", "reds", "--reds: red players (player killers)")}
        {flag(
          "prior",
          "live prior",
          prior.available
            ? `--prior ${prior.path}: start every career's memory with the live store's hunt history`
            : `unavailable: no hunt history in the live store${prior.path ? ` (${prior.path})` : ""}`,
          !prior.available,
        )}
        {flag("fresh", "fresh memory per run", "--fresh: every run on a fresh memory store (no careers)")}
        {flag("trace", "record traces", "--trace: record each run's trace for this page (logs/sim_traces)")}
      </div>
      {form.trace && runs > MANY_TRACES && (
        <p className="warn sim-note">
          Recording traces writes one trace file per run: {runs} files.
        </p>
      )}
      <label className="sim-field">
        <span>--set (one module.NAME=value per line)</span>
        <textarea
          rows={2}
          value={form.sets}
          placeholder="tamer.FALLBACK_S=0"
          spellCheck={false}
          onChange={(e) => {
            const v = e.target.value;
            upd((f) => ({ ...f, sets: v }));
          }}
        />
      </label>
      <label className="sim-field">
        <span>runner args (after --)</span>
        <input
          type="text"
          value={form.runner_args}
          placeholder={PARITY_RUNNER_ARGS}
          spellCheck={false}
          onChange={(e) => {
            const v = e.target.value;
            upd((f) => ({ ...f, runner_args: v }));
          }}
        />
      </label>
      <div className="sim-form-row">
        <button
          type="button"
          onClick={() => upd(() => parityForm(opts.run.defaults, prior.available))}
          title={`docs/SIM.md's parity batch: --runs 144 --seed 17 --others --eerie --prior -- ${PARITY_RUNNER_ARGS} (no traces)`}
        >
          parity batch
        </button>
        <button type="button" onClick={() => upd(() => runFormFrom(opts.run.defaults))} title="run.py's defaults">
          defaults
        </button>
      </div>
    </>
  );
}

function FightFields({ form, upd, fights }: { form: FightForm; upd: Update<FightForm>; fights: readonly SimFight[] }) {
  const fight = fights.find((x) => x.name === form.fight);
  const seeds = countSeeds(form.seeds);
  if (!fights.length) return <p className="dim">The server lists no fight scripts.</p>;
  return (
    <>
      <label className="sim-field">
        <span>fight</span>
        <select
          value={form.fight}
          onChange={(e) => {
            const next = fights.find((x) => x.name === e.target.value);
            upd((f) => fightFormFrom(next, f.trace, f.seeds));
          }}
        >
          {fights.map((x) => (
            <option key={x.name} value={x.name}>
              {x.name}
              {x.title ? ` — ${x.title}` : ""}
            </option>
          ))}
        </select>
      </label>
      {fight && <p className="dim mono sim-note">{fight.usage}</p>}
      <div className="sim-form-row">
        <label className="sim-field" title="seeds and ranges, e.g. 1,2,5-8">
          <span>seeds{seeds.ok ? ` (${seeds.value})` : ""}</span>
          <input
            type="text"
            value={form.seeds}
            placeholder="1,2,5-8"
            spellCheck={false}
            onChange={(e) => {
              const v = e.target.value;
              upd((f) => ({ ...f, seeds: v }));
            }}
          />
        </label>
        {fight?.options.map((o, i) =>
          o.type === "flag" ? (
            <Check
              key={o.arg}
              checked={!!form.flags[o.arg]}
              onChange={(v) => upd((f) => ({ ...f, flags: { ...f.flags, [o.arg]: v } }))}
              label={o.arg}
              title={o.arg}
            />
          ) : (
            <label key={`choice${i}`} className="sim-field">
              <span>{o.args.join(" | ")}</span>
              <select
                value={form.choices[i] ?? ""}
                onChange={(e) => {
                  const v = e.target.value;
                  upd((f) => ({ ...f, choices: { ...f.choices, [i]: v } }));
                }}
              >
                <option value="">(none)</option>
                {o.args.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </label>
          ),
        )}
        <Check checked={form.trace} onChange={(v) => upd((f) => ({ ...f, trace: v }))} label="record traces" title="--trace: one trace per seed for this page" />
      </div>
      {form.trace && seeds.ok && seeds.value > MANY_TRACES && <p className="warn sim-note">Recording traces writes one trace file per seed: {seeds.value} files.</p>}
    </>
  );
}

/** A job's output, kept scrolled to the bottom unless the user scrolled up. */
function JobOut({ lines }: { lines: string[] }) {
  const ref = useRef<HTMLPreElement>(null);
  const stick = useRef(true);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [lines]);
  return (
    <pre
      ref={ref}
      className="sim-job-out"
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      }}
    >
      {lines.length ? lines.join("\n") : "(no output yet)"}
    </pre>
  );
}

function JobCard({ job, expanded, current, onCancel, onLoad }: { job: SimJob; expanded: boolean; current: string | null; onCancel: (id: number) => Promise<void>; onLoad: (name: string) => void }) {
  const [open, setOpen] = useState(expanded);
  useEffect(() => setOpen(expanded), [expanded]);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const took = (job.ended ?? Date.now() / 1000) - job.started;
  return (
    <details className="sim-job" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>
        <span className="sim-job-label">
          #{job.id} {job.label}
        </span>
        <Badge kind={STATUS_BADGE[job.status]}>{job.status}</Badge>
        <span className="dim" title={`started ${new Date(job.started * 1000).toLocaleString()}`}>
          {new Date(job.started * 1000).toLocaleTimeString()} · {fmtDuration(took)}
          {job.exit !== null ? ` · exit ${job.exit}` : ""}
          {job.traces.length ? ` · ${job.traces.length} trace${job.traces.length > 1 ? "s" : ""}` : ""}
        </span>
      </summary>
      {open && (
        <>
          <div className="sim-form-row">
            <code className="sim-job-argv">{job.argv.join(" ")}</code>
            {job.status === "running" && (
              <button
                type="button"
                className="danger"
                disabled={cancelling}
                onClick={() => {
                  setCancelling(true);
                  setCancelError(null);
                  onCancel(job.id)
                    .catch((e: unknown) => setCancelError(errText(e)))
                    .finally(() => setCancelling(false));
                }}
              >
                Cancel
              </button>
            )}
            {cancelError && <span className="error">{cancelError}</span>}
          </div>
          <JobOut lines={job.out} />
          {job.traces.length > 0 && (
            <div className="sim-job-traces">
              {job.traces.map((n) => (
                <button key={n} type="button" className={n === current ? "link mono active" : "link mono"} title="load this trace" onClick={() => onLoad(n)}>
                  {n}
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </details>
  );
}

export function SimRunPanel({ open, onClose, sim, current, onLoad }: { open: boolean; onClose: () => void; sim: SimJobs; current: string | null; onLoad: (name: string) => void }) {
  const [opts, setOpts] = useState<SimOptions | null>(null);
  const [optsError, setOptsError] = useState<string | null>(null);
  const [kind, setKind] = useState<"run" | "fight">("run");
  const [run, setRun] = useState<RunForm | null>(null);
  const [fight, setFight] = useState<FightForm | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const loadOptions = useCallback(() => {
    setOptsError(null);
    fetchSimOptions().then(
      (o) => {
        setOpts(o);
        setRun((r) => r ?? runFormFrom(o.run.defaults));
        setFight((f) => f ?? fightFormFrom(o.fights[0], o.run.defaults.trace));
      },
      (e: unknown) => setOptsError(errText(e)),
    );
  }, []);
  // fetched on the first open (and on each reopen until it succeeds)
  useEffect(() => {
    if (open && !opts) loadOptions();
  }, [open, opts, loadOptions]);

  const updRun: Update<RunForm> = useCallback((fn) => setRun((r) => r && fn(r)), []);
  const updFight: Update<FightForm> = useCallback((fn) => setFight((f) => f && fn(f)), []);

  const req = !opts ? null : kind === "run" ? run && runRequest(run, opts.run.limits, opts.prior.available) : fight && fightRequest(opts.fights, fight);
  const start = () => {
    if (!req?.ok) return;
    setStarting(true);
    setStartError(null);
    sim
      .start(req.value)
      .catch((e: unknown) => setStartError(errText(e)))
      .finally(() => setStarting(false));
  };

  return (
    <div className="sim-runs-layer" style={open ? undefined : { display: "none" }}>
      <Panel
        title="Sim jobs"
        className="sim-runs"
        extra={
          <button type="button" onClick={onClose} title="close">
            ✕
          </button>
        }
      >
        {optsError && (
          <p className="error">
            {optsError}{" "}
            <button type="button" onClick={loadOptions}>
              retry
            </button>
          </p>
        )}
        {!opts && !optsError && <p className="dim">loading options…</p>}
        {opts && run && fight && (
          <div className="sim-form">
            <nav className="page-switch" aria-label="job kind">
              <button type="button" className={kind === "run" ? "active" : undefined} onClick={() => setKind("run")}>
                run.py
              </button>
              <button type="button" className={kind === "fight" ? "active" : undefined} onClick={() => setKind("fight")}>
                scripted fight
              </button>
            </nav>
            {kind === "run" ? <RunFields form={run} upd={updRun} opts={opts} /> : <FightFields form={fight} upd={updFight} fights={opts.fights} />}
            <div className="sim-form-row">
              <button
                type="button"
                className="active"
                disabled={!req?.ok || starting || sim.running !== null}
                title={sim.running ? `job #${sim.running.id} is running (one at a time)` : undefined}
                onClick={start}
              >
                Start
              </button>
              {req && !req.ok && <span className="warn">{req.error}</span>}
              {startError && <span className="error">{startError}</span>}
            </div>
          </div>
        )}
        <h3 className="sim-sub">Jobs</h3>
        {sim.error && <p className="error">{sim.error}</p>}
        {sim.jobs && !sim.jobs.length && <p className="dim">No jobs yet.</p>}
        {sim.jobs?.map((j, i) => (
          <JobCard key={j.id} job={j} expanded={i === 0} current={current} onCancel={sim.cancel} onLoad={onLoad} />
        ))}
      </Panel>
    </div>
  );
}

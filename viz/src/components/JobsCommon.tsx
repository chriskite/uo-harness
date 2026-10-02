// Shared by the job dashboards (docs/VISUALIZER.md §2.4): polling /api/jobs and the
// page head with the job switch.
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { fmtStamp, tzMinutesEast } from "../jobs.ts";
import { Badge } from "./common.tsx";

export const JOBS = [
  { key: "lumber", label: "Lumber" },
  { key: "hunt", label: "Hunting" },
] as const;
export type JobKind = (typeof JOBS)[number]["key"];

const REFRESH_MS = 15_000;

/** Polls `fetch(tz)` every 15 s; `reload` refreshes now. `fetch` must be stable
 *  (module-level): a new function each render would restart the poll every render. */
export function useJobPoll<T>(fetch: (tz: number) => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [at, setAt] = useState<number | null>(null);

  const reload = useCallback(() => {
    fetch(tzMinutesEast()).then(
      (d) => {
        setData(d);
        setError(null);
        setAt(Date.now() / 1000);
      },
      (e: unknown) => setError(String(e)),
    );
  }, [fetch]);

  useEffect(() => {
    reload();
    const t = setInterval(reload, REFRESH_MS);
    return () => clearInterval(t);
  }, [reload]);

  return { data, error, at, reload };
}

export function JobSwitch({ job, onJob }: { job: JobKind; onJob: (j: JobKind) => void }) {
  return (
    <nav className="page-switch" aria-label="job">
      {JOBS.map((j) => (
        <button key={j.key} type="button" className={j.key === job ? "active" : undefined} onClick={() => onJob(j.key)}>
          {j.label}
        </button>
      ))}
    </nav>
  );
}

export function JobsHead({
  job,
  onJob,
  title,
  store,
  error,
  at,
  onRefresh,
}: {
  job: JobKind;
  onJob: (j: JobKind) => void;
  title: ReactNode;
  store: boolean;
  error: string | null;
  at: number | null;
  onRefresh: () => void;
}) {
  return (
    <div className="jobs-head">
      <JobSwitch job={job} onJob={onJob} />
      <h3>{title}</h3>
      {!store && <Badge kind="warn">no memory store: nothing recorded yet</Badge>}
      {error && <span className="error">{error}</span>}
      <span className="spacer" />
      <span className="dim mono">{at ? `updated ${fmtStamp(at, true)}` : ""}</span>
      <button type="button" onClick={onRefresh}>
        ↻ refresh
      </button>
    </div>
  );
}

/** Loading / error placeholder before the first answer, with the switch so the job can still change. */
export function JobsLoading({ job, onJob, error }: { job: JobKind; onJob: (j: JobKind) => void; error: string | null }) {
  return (
    <div className="jobs pad">
      <div className="jobs-head">
        <JobSwitch job={job} onJob={onJob} />
        {error ? <span className="error">{error}</span> : <span className="dim">loading job analytics…</span>}
      </div>
    </div>
  );
}

// Shared by the job dashboards (docs/VISUALIZER.md §2.4): polling /api/jobs over the
// page's date range, and the page head with the job switch and the range picker.
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { fmtStamp, localDay, parseRange, presetRange, RANGE_PRESETS, rangeBounds, rangeQuery, tzMinutesEast, type DateRange, type RangeBounds } from "../jobs.ts";
import { Badge } from "./common.tsx";

export const JOBS = [
  { key: "lumber", label: "Lumber" },
  { key: "hunt", label: "Hunting" },
] as const;
export type JobKind = (typeof JOBS)[number]["key"];

/** What a job dashboard gets from the page: the job switch and the shared date range. */
export interface JobDashboardProps {
  onJob: (j: JobKind) => void;
  range: DateRange;
  onRange: (r: DateRange) => void;
}

export const REFRESH_MS = 15_000;

/** Polls `fetch(tz, bounds)` every 15 s for `range`; `reload` refreshes now. A range
 *  change fetches at once; until it answers, `data` is the old range's and `loading`
 *  is true, and answers for a range no longer shown are dropped. `fetch` must be
 *  stable (module-level): a new function each render would restart the poll every render. */
export function useJobPoll<T>(fetch: (tz: number, range: RangeBounds) => Promise<T>, range: DateRange) {
  const key = rangeQuery(range);
  const [data, setData] = useState<T | null>(null);
  const [dataKey, setDataKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [at, setAt] = useState<number | null>(null);
  const current = useRef(key);

  const reload = useCallback(() => {
    fetch(tzMinutesEast(), rangeBounds(parseRange(key))).then(
      (d) => {
        if (current.current !== key) return;
        setData(d);
        setDataKey(key);
        setError(null);
        setAt(Date.now() / 1000);
      },
      (e: unknown) => {
        if (current.current === key) setError(String(e));
      },
    );
  }, [fetch, key]);

  useEffect(() => {
    current.current = key;
    reload();
    const t = setInterval(reload, REFRESH_MS);
    return () => clearInterval(t);
  }, [reload, key]);

  return { data, error, at, reload, loading: dataKey !== key };
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

/** Presets (open-ended, ending today) and a from/to pair of local days, both inclusive. */
export function RangePicker({ range, onRange, loading }: { range: DateRange; onRange: (r: DateRange) => void; loading: boolean }) {
  const today = localDay(Date.now() / 1000);
  const key = rangeQuery(range);
  return (
    <div className="jobs-range">
      <nav className="page-switch" aria-label="date range">
        {RANGE_PRESETS.map((p) => (
          <button
            key={p.key}
            type="button"
            className={rangeQuery(presetRange(p.key, today)) === key ? "active" : undefined}
            onClick={() => onRange(presetRange(p.key, today))}
          >
            {p.label}
          </button>
        ))}
      </nav>
      <label>
        from <input type="date" value={range.from ?? ""} max={range.to ?? undefined} onChange={(e) => onRange({ ...range, from: e.target.value || null })} />
      </label>
      <label>
        to <input type="date" value={range.to ?? ""} min={range.from ?? undefined} onChange={(e) => onRange({ ...range, to: e.target.value || null })} />
      </label>
      <span className="dim">{loading ? "loading…" : range.from || range.to ? "trips by start, events by time" : "all recorded history"}</span>
    </div>
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
  range,
  onRange,
  loading,
}: {
  job: JobKind;
  onJob: (j: JobKind) => void;
  title: ReactNode;
  store: boolean;
  error: string | null;
  at: number | null;
  onRefresh: () => void;
  range: DateRange;
  onRange: (r: DateRange) => void;
  loading: boolean;
}) {
  return (
    <>
      <div className="jobs-head">
        <JobSwitch job={job} onJob={onJob} />
        <h3>{title}</h3>
        {!store && <Badge kind="warn">no Codex: nothing recorded yet</Badge>}
        {error && <span className="error">{error}</span>}
        <span className="spacer" />
        <span className="dim mono">{at ? `updated ${fmtStamp(at, true)}` : ""}</span>
        <button type="button" onClick={onRefresh}>
          ↻ refresh
        </button>
      </div>
      <RangePicker range={range} onRange={onRange} loading={loading} />
    </>
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

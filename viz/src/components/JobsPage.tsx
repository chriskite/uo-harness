import { fetchJobs } from "../api.ts";
import { fmtDuration } from "../format.ts";
import { eventView, fmtGp, fmtNum, fmtStamp, kpis, oneLocalDay, phaseList, woodShares } from "../jobs.ts";
import { EventStrip, LogsPerTripChart, RateChart } from "./Charts.tsx";
import { Badge, Panel } from "./common.tsx";
import { HuntJobs } from "./HuntJobs.tsx";
import { JobsHead, JobsLoading, useJobPoll, type JobKind } from "./JobsCommon.tsx";

const fetchLumber = (tz: number) => fetchJobs("lumber", tz);

/** The Jobs page: one dashboard per job, picked by the switch in its head. */
export function JobsPage({ job, onJob }: { job: JobKind; onJob: (j: JobKind) => void }) {
  return job === "hunt" ? <HuntJobs onJob={onJob} /> : <LumberJobs onJob={onJob} />;
}

/** Lumber job dashboard from /api/jobs?job=lumber (harness/jobs.py compute). */
function LumberJobs({ onJob }: { onJob: (j: JobKind) => void }) {
  const { data, error, at, reload } = useJobPoll(fetchLumber);
  if (!data) return <JobsLoading job="lumber" onJob={onJob} error={error} />;
  const t = data.totals;
  const trips = data.trips;
  const sameDay = oneLocalDay(trips.flatMap((r) => (r.t_start === null ? [] : [r.t_start])));
  const shares = woodShares(data.woods);
  const priced = data.woods.filter((w) => w.value_gp !== null);
  const windowMin = Math.round(data.window_s / 60);
  return (
    <div className="jobs">
      <JobsHead
        job="lumber"
        onJob={onJob}
        title={
          <>
            Lumber job <span className="dim">· {t.trips ? `${fmtStamp(t.first_t!)} → ${fmtStamp(t.last_t!)}` : "no trips recorded"}</span>
          </>
        }
        store={data.store}
        error={error}
        at={at}
        onRefresh={reload}
      />

      <div className="kpis">
        {kpis(t).map((k) => (
          <div key={k.key} className={`kpi kpi-${k.tone}`}>
            <span className="kpi-label">{k.label}</span>
            <span className="kpi-value">{k.value}</span>
            <span className="kpi-sub">{k.sub ?? " "}</span>
          </div>
        ))}
      </div>
      <div className="jobs-sub dim">
        estimated value <span className="mono">{fmtGp(t.value_gp)}</span>
        {t.value_unpriced_logs > 0 && ` (${t.value_unpriced_logs} logs unpriced${data.woods_file ? "" : ": no harness/data/woods.json"})`}
        {data.harvest && (
          <>
            {" · "}chop attempts: {data.harvest.success} success / {data.harvest.fail} fail / {data.harvest.depleted} depleted /{" "}
            {data.harvest.unreachable} unreachable{data.harvest.not_tree ? ` / ${data.harvest.not_tree} not a tree` : ""}
            {data.harvest.success_rate !== null && ` (${Math.round(data.harvest.success_rate * 100)}% land)`}
          </>
        )}
      </div>

      <div className="jobs-charts">
        <Panel title="Logs / hr over time">
          <RateChart
            points={data.rolling}
            series={[
              { label: `rolling ${windowMin} min`, cls: "roll", values: data.rolling.map((p) => p.logs_per_hour), dots: true },
              { label: "cumulative", cls: "cum", values: data.rolling.map((p) => (p.logs_per_hour === null ? null : p.cum_logs_per_hour)) },
            ]}
            what="trip"
            unit="logs/hr"
            note="active time only; one point per trip end"
          />
        </Panel>
        <Panel title="Logs per trip">
          <LogsPerTripChart trips={trips} />
        </Panel>
      </div>

      <div className="jobs-charts">
        <Panel title={`Deaths, thefts, PKs, flees (${data.events.length})`}>
          <EventStrip spans={trips.map((r) => ({ n: r.n, t_start: r.t_start, t_end: r.t_end, label: `trip #${r.n} · ${r.logs} logs` }))} events={data.events} />
          {data.events.length === 0 ? (
            <p className="dim">No deaths, thefts, PK sightings or flees recorded.</p>
          ) : (
            <ol className="job-events">
              {[...data.events].reverse().map((e, i) => {
                const v = eventView(e);
                return (
                  <li key={e.id ?? i}>
                    <span className="mono dim">{fmtStamp(e.t)}</span>
                    <Badge kind={v.tone}>{v.label}</Badge>
                    <span className="dim">{v.detail}</span>
                  </li>
                );
              })}
            </ol>
          )}
        </Panel>
        <Panel title="Wood types">
          {shares.length === 0 ? (
            <p className="dim">No per-wood breakdown in the trip rows yet (trip rows carry `woods` once the runner records them).</p>
          ) : (
            <div className="woods">
              {shares.map((w) => (
                <div key={w.name} className="wood-row">
                  <span>{w.name}</span>
                  <div className="bar-track">
                    <div className="bar-fill wood-fill" style={{ width: `${w.share * 100}%` }} />
                  </div>
                  <span className="mono num">
                    {w.logs} · {Math.round(w.share * 100)}%
                  </span>
                  <span className="mono num dim">{fmtGp(w.total_gp)}</span>
                </div>
              ))}
            </div>
          )}
          <p className="dim small">
            {data.woods_file
              ? `woods.json: ${data.woods.length} woods, ${priced.length} with a value`
              : "woods.json not found: values unknown"}
          </p>
        </Panel>
      </div>

      <Panel title={`Trips (${trips.length})`}>
        {trips.length === 0 ? (
          <p className="dim">No trips yet. Rows appear here after each lumber trip (recorded in the Codex).</p>
        ) : (
          <div className="table-wrap">
            <table className="counts trips-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>start</th>
                  <th className="num">time</th>
                  <th className="num">logs</th>
                  <th className="num">stored</th>
                  <th className="num">logs/hr</th>
                  <th className="num">chops</th>
                  <th className="num">captchas</th>
                  <th>phases (harvest · convert · to bank · store)</th>
                  <th>events</th>
                  <th className="num">value</th>
                </tr>
              </thead>
              <tbody>
                {[...trips].reverse().map((r) => (
                  <tr key={r.n}>
                    <td className="mono">{r.n}</td>
                    <td className="mono">{r.t_start ? fmtStamp(r.t_start, sameDay) : "—"}</td>
                    <td className="mono num">{r.duration_s === null ? "—" : fmtDuration(r.duration_s)}</td>
                    <td className="mono num">{r.logs}</td>
                    <td className="mono num">{r.stored}</td>
                    <td className="mono num">{fmtNum(r.logs_per_hour, 0)}</td>
                    <td className="mono num" title="successful chops / attempts">
                      {r.successes}/{r.attempts}
                    </td>
                    <td className={r.captchas ? "mono num warn" : "mono num"} title={r.captchas ? `${fmtNum(r.captcha_wait_s)} s waiting` : undefined}>
                      {r.captchas}
                    </td>
                    <td className="mono dim">
                      {phaseList(r.phases_s)
                        .map(([, s]) => fmtDuration(s))
                        .join(" · ")}
                    </td>
                    <td>
                      {Object.entries(r.events).map(([k, n]) => (
                        <Badge key={k} kind={k === "death" ? "bad" : "warn"}>
                          {k} {n}
                        </Badge>
                      ))}
                    </td>
                    <td className="mono num">{fmtGp(r.value_gp)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {data.days.length > 0 && (
        <Panel title="Per day">
          <table className="counts">
            <thead>
              <tr>
                <th>day</th>
                <th className="num">trips</th>
                <th className="num">logs</th>
                <th className="num">active</th>
                <th className="num">logs/hr</th>
                <th className="num">logs/trip</th>
                <th className="num">captchas</th>
                <th className="num">deaths pk/mob</th>
                <th className="num">thefts</th>
                <th className="num">value</th>
              </tr>
            </thead>
            <tbody>
              {data.days.map((d) => (
                <tr key={d.day}>
                  <td className="mono">{d.day}</td>
                  <td className="mono num">{d.trips}</td>
                  <td className="mono num">{d.logs}</td>
                  <td className="mono num">{fmtDuration(d.active_s)}</td>
                  <td className="mono num">{fmtNum(d.logs_per_hour, 0)}</td>
                  <td className="mono num">{fmtNum(d.logs_per_trip, 1)}</td>
                  <td className="mono num">{d.captchas}</td>
                  <td className="mono num">
                    {d.deaths.pk}/{d.deaths.mob}
                  </td>
                  <td className="mono num">
                    {d.thefts.count}
                    {d.thefts.amount ? ` (${d.thefts.amount})` : ""}
                  </td>
                  <td className="mono num">{fmtGp(d.value_gp)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </div>
  );
}

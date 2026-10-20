import { fmtDuration } from "../format.ts";
import { eventView, fmtInt, fmtNum, fmtStamp, legEnd, miningKpis, oneLocalDay } from "../jobs.ts";
import { EventStrip } from "./Charts.tsx";
import { Badge, Panel } from "./common.tsx";
import { fetchMining, JobsHead, JobsLoading, useJobPoll, type JobDashboardProps } from "./JobsCommon.tsx";

/** Mining job dashboard from /api/jobs?job=mining (harness/jobs.py compute_mining). */
export function MiningJobs({ onJob, range, onRange }: JobDashboardProps) {
  const { data, error, at, reload, loading } = useJobPoll(fetchMining, range);
  if (!data) return <JobsLoading job="mining" onJob={onJob} error={error} />;
  const t = data.totals;
  const legs = data.legs;
  const carried = data.carried_now;
  const sameDay = oneLocalDay(legs.flatMap((l) => (l.t_start === null ? [] : [l.t_start])));
  const maxOre = Math.max(1, ...data.spots.map((s) => s.ore));
  return (
    <div className="jobs">
      <JobsHead
        job="mining"
        onJob={onJob}
        title={
          <>
            Mining job <span className="dim">· {legs.length > 0 && t.first_t !== null ? `${fmtStamp(t.first_t)} → ${fmtStamp(t.last_t!)}` : data.since > 0 || data.until !== null ? "no legs in this range" : "no legs recorded"}</span>
          </>
        }
        store={data.store}
        error={error}
        at={at}
        onRefresh={reload}
        range={range}
        onRange={onRange}
        loading={loading}
      />

      <div className="kpis">
        {miningKpis(t, carried).map((k) => (
          <div key={k.key} className={`kpi kpi-${k.tone}`} title={k.hint}>
            <span className="kpi-label">{k.label}</span>
            <span className="kpi-value">{k.value}</span>
            <span className="kpi-sub">{k.sub ?? " "}</span>
          </div>
        ))}
      </div>
      <div className="jobs-sub dim">
        Field time is the dig phase less the walk out and the travel lockout (the planner's measure, mining_opt.trip_obs). Ore is smelted at the
        spot's forge; at each trip's end the runner puts the ingots into the room's Resource Stockpile, so carried is what was left in the pack.
        {carried && ` · ${carried.label}: ${fmtInt(carried.ingots)}${carried.t === null ? "" : ` as of ${fmtStamp(carried.t)}`}${carried.spot ? ` (${carried.spot})` : ""}.`}
      </div>

      <div className="jobs-charts">
        <Panel title={`Spots (${data.spots.length})`}>
          {data.spots.length === 0 ? (
            <p className="dim">No legs at any spot yet.</p>
          ) : (
            <table className="counts">
              <thead>
                <tr>
                  <th>spot</th>
                  <th />
                  <th className="num">legs</th>
                  <th className="num">ore</th>
                  <th className="num">ore / field hr</th>
                  <th className="num">field</th>
                  <th className="num">dry</th>
                  <th className="num">last leg</th>
                </tr>
              </thead>
              <tbody>
                {data.spots.map((s) => (
                  <tr key={s.spot}>
                    <td className="mono">{s.spot}</td>
                    <td className="monster-bar">
                      <div className="bar-track">
                        <div className="bar-fill monster-fill" style={{ width: `${(s.ore / maxOre) * 100}%` }} />
                      </div>
                    </td>
                    <td className="mono num">{s.legs}</td>
                    <td className="mono num">{fmtInt(s.ore)}</td>
                    <td className="mono num">{fmtNum(s.ore_per_hour, 0)}</td>
                    <td className="mono num">{fmtDuration(s.field_s)}</td>
                    <td className="mono num">{s.dry}</td>
                    <td className="mono num">{s.last_t === null ? "—" : fmtStamp(s.last_t)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
        <Panel title={`Threats and hops (${data.events.length})`}>
          <EventStrip spans={legs.map((l) => ({ n: l.n, t_start: l.t_start, t_end: l.t_end, label: `leg #${l.n} · ${l.ore} ore` }))} events={data.events} />
          {data.events.length === 0 ? (
            <p className="dim">No deaths, threats or hops recorded.</p>
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
      </div>

      <Panel title={`Legs (${legs.length})`}>
        {legs.length === 0 ? (
          <p className="dim">No mining legs yet. Rows appear here after each leg of a mining run (recorded in the Codex).</p>
        ) : (
          <div className="table-wrap">
            <table className="counts trips-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>start</th>
                  <th>spot</th>
                  <th className="num">trip · leg</th>
                  <th className="num">field</th>
                  <th className="num">ore</th>
                  <th className="num">ore/hr</th>
                  <th className="num">digs (land)</th>
                  <th className="num">smelts</th>
                  <th className="num">ingots made</th>
                  <th className="num">stored</th>
                  <th className="num">carried</th>
                  <th>ended</th>
                  <th>why</th>
                </tr>
              </thead>
              <tbody>
                {[...legs].reverse().map((l) => {
                  const end = legEnd(l);
                  return (
                    <tr key={l.n}>
                      <td className="mono">{l.n}</td>
                      <td className="mono">{l.t_start ? fmtStamp(l.t_start, sameDay) : "—"}</td>
                      <td className="mono">{l.spot ?? "—"}</td>
                      <td className="mono num">
                        {l.trip ?? "—"} · {l.leg ?? "—"}
                      </td>
                      <td className="mono num">{l.field_s === null ? "—" : fmtDuration(l.field_s)}</td>
                      <td className="mono num">{fmtInt(l.ore)}</td>
                      <td className="mono num">{fmtNum(l.ore_per_hour, 0)}</td>
                      <td className="mono num">
                        {l.attempts} ({l.successes})
                      </td>
                      <td className="mono num">{l.smelts}</td>
                      <td className="mono num">{fmtInt(l.ingots_made)}</td>
                      <td className="mono num">{fmtInt(l.ingots_stored)}</td>
                      <td className="mono num">{fmtInt(l.ingots_carried)}</td>
                      <td>
                        <Badge kind={end.tone}>{end.text}</Badge>
                      </td>
                      <td className="dim">{l.why || "—"}</td>
                    </tr>
                  );
                })}
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
                <th className="num">legs</th>
                <th className="num">field</th>
                <th className="num">ore</th>
                <th className="num">ore / field hr</th>
                <th className="num">digs land</th>
                <th className="num">smelts</th>
                <th className="num">ingots made</th>
                <th className="num">ingots stored</th>
                <th className="num">deaths · threats</th>
              </tr>
            </thead>
            <tbody>
              {data.days.map((d) => (
                <tr key={d.day}>
                  <td className="mono">{d.day}</td>
                  <td className="mono num">{d.legs}</td>
                  <td className="mono num">{fmtDuration(d.field_s)}</td>
                  <td className="mono num">{fmtInt(d.ore)}</td>
                  <td className="mono num">{fmtNum(d.ore_per_hour, 0)}</td>
                  <td className="mono num">{d.success_rate === null ? "—" : `${Math.round(d.success_rate * 100)}%`}</td>
                  <td className="mono num">{d.smelts}</td>
                  <td className="mono num">{fmtInt(d.ingots_made)}</td>
                  <td className="mono num">{fmtInt(d.ingots_stored)}</td>
                  <td className="mono num">
                    {d.deaths} · {d.sent_home}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </div>
  );
}

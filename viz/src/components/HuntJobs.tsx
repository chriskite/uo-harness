import { fetchJobs } from "../api.ts";
import { fmtDuration } from "../format.ts";
import { eventView, fmtGp, fmtInt, fmtNum, fmtStamp, huntKpis, oneLocalDay } from "../jobs.ts";
import { EventStrip, RateChart, VisitBarsChart } from "./Charts.tsx";
import { Badge, Panel } from "./common.tsx";
import { JobsHead, JobsLoading, useJobPoll, type JobKind } from "./JobsCommon.tsx";

const fetchHunt = (tz: number) => fetchJobs("hunt", tz);

/** Hunting job dashboard from /api/jobs?job=hunt (harness/jobs.py compute_hunt). */
export function HuntJobs({ onJob }: { onJob: (j: JobKind) => void }) {
  const { data, error, at, reload } = useJobPoll(fetchHunt);
  if (!data) return <JobsLoading job="hunt" onJob={onJob} error={error} />;
  const t = data.totals;
  const visits = data.visits;
  const sameDay = oneLocalDay(visits.flatMap((v) => (v.t_start === null ? [] : [v.t_start])));
  const windowMin = Math.round(data.window_s / 60);
  const recorded = t.visits > 0 || t.kills > 0;
  const outside = t.outside_visits;
  const maxKills = Math.max(1, ...data.monsters.map((m) => m.kills));
  return (
    <div className="jobs">
      <JobsHead
        job="hunt"
        onJob={onJob}
        title={
          <>
            Hunting job <span className="dim">· {recorded && t.first_t !== null ? `${fmtStamp(t.first_t)} → ${fmtStamp(t.last_t!)}` : "no visits recorded"}</span>
          </>
        }
        store={data.store}
        error={error}
        at={at}
        onRefresh={reload}
      />

      <div className="kpis">
        {huntKpis(t).map((k) => (
          <div key={k.key} className={`kpi kpi-${k.tone}`}>
            <span className="kpi-label">{k.label}</span>
            <span className="kpi-value">{k.value}</span>
            <span className="kpi-sub">{k.sub ?? " "}</span>
          </div>
        ))}
      </div>
      <div className="jobs-sub dim">
        XP is mastery-chain experience, estimated as the gold each looted corpse held (a kill's creature gold value; solo, full
        damage share). Kills never looted have unknown XP.
        {outside.kills > 0 &&
          ` · ${outside.kills} kill${outside.kills === 1 ? "" : "s"} (${fmtGp(outside.gold)}, ${fmtInt(outside.xp)} XP) fell outside any recorded visit (a run stopped before its visit row): counted in the totals, not in the rates.`}
      </div>

      <div className="jobs-charts">
        <Panel title="Gold / XP per hour over time">
          <RateChart
            points={data.rolling}
            series={[
              { label: `XP/hr rolling ${windowMin} min`, cls: "xp", values: data.rolling.map((p) => p.xp_per_hour), dots: true },
              { label: `gold/hr rolling ${windowMin} min`, cls: "gold", values: data.rolling.map((p) => p.gold_per_hour), dots: true },
              { label: "XP/hr cumulative", cls: "cum", values: data.rolling.map((p) => p.cum_xp_per_hour) },
            ]}
            what="visit"
            unit="/hr"
            note="active time only; one point per visit end"
          />
        </Panel>
        <Panel title="XP and gold per visit">
          <VisitBarsChart visits={visits} />
        </Panel>
      </div>

      <div className="jobs-charts">
        <Panel title={`Deaths, leaves, speech holds (${data.events.length})`}>
          <EventStrip spans={visits.map((v) => ({ n: v.n, t_start: v.t_start, t_end: v.t_end, label: `visit #${v.n} · ${v.kills} kills` }))} events={data.events} />
          {data.events.length === 0 ? (
            <p className="dim">No deaths, leaves or speech holds recorded.</p>
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
        <Panel title="Monsters">
          {data.monsters.length === 0 ? (
            <p className="dim">No kills yet.</p>
          ) : (
            <table className="counts">
              <thead>
                <tr>
                  <th>monster</th>
                  <th />
                  <th className="num">kills</th>
                  <th className="num">looted</th>
                  <th className="num">gold</th>
                  <th className="num">XP</th>
                  <th className="num">XP / kill</th>
                </tr>
              </thead>
              <tbody>
                {data.monsters.map((m) => (
                  <tr key={m.name}>
                    <td>{m.name}</td>
                    <td className="monster-bar">
                      <div className="bar-track">
                        <div className="bar-fill monster-fill" style={{ width: `${(m.kills / maxKills) * 100}%` }} />
                      </div>
                    </td>
                    <td className="mono num">{m.kills}</td>
                    <td className="mono num">{m.looted}</td>
                    <td className="mono num">{fmtGp(m.gold)}</td>
                    <td className="mono num">{fmtInt(m.xp)}</td>
                    <td className="mono num">{fmtNum(m.xp_per_kill, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>

      <Panel title={`Visits (${visits.length})`}>
        {visits.length === 0 ? (
          <p className="dim">No visits yet. Rows appear here after each visit to the hunting spot (Memory.episode, loop hunt).</p>
        ) : (
          <div className="table-wrap">
            <table className="counts trips-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>start</th>
                  <th className="num">time</th>
                  <th className="num">kills</th>
                  <th className="num">gold</th>
                  <th className="num">XP</th>
                  <th className="num">XP/hr</th>
                  <th className="num">hits lost</th>
                  <th className="num">casts</th>
                  <th className="num">heals · potions</th>
                  <th>ended</th>
                  <th>events</th>
                </tr>
              </thead>
              <tbody>
                {[...visits].reverse().map((v) => (
                  <tr key={v.n}>
                    <td className="mono">{v.n}</td>
                    <td className="mono">{v.t_start ? fmtStamp(v.t_start, sameDay) : "—"}</td>
                    <td className="mono num">{v.duration_s === null ? "—" : fmtDuration(v.duration_s)}</td>
                    <td className="mono num">{v.kills}</td>
                    <td className="mono num">{fmtGp(v.gold)}</td>
                    <td className="mono num" title={v.kills > v.xp_kills ? `${v.kills - v.xp_kills} kill(s) not looted: XP unknown` : undefined}>
                      {fmtInt(v.xp)}
                      {v.kills > v.xp_kills ? "+" : ""}
                    </td>
                    <td className="mono num">{fmtInt(v.xp_per_hour)}</td>
                    <td className="mono num">{v.hits_lost}</td>
                    <td className="mono num">{v.casts}</td>
                    <td className="mono num">
                      {v.heals} · {v.potions}
                    </td>
                    <td className="dim">{v.ended ?? "—"}</td>
                    <td>
                      {Object.entries(v.events).map(([k, n]) => (
                        <Badge key={k} kind={k === "death" ? "bad" : k === "speech_clear" ? "ok" : "warn"}>
                          {k} {n}
                        </Badge>
                      ))}
                    </td>
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
                <th className="num">visits</th>
                <th className="num">active</th>
                <th className="num">kills</th>
                <th className="num">gold</th>
                <th className="num">XP</th>
                <th className="num">gold / kill</th>
                <th className="num">hits lost</th>
                <th className="num">deaths pk/mob</th>
                <th className="num">speech holds</th>
              </tr>
            </thead>
            <tbody>
              {data.days.map((d) => (
                <tr key={d.day}>
                  <td className="mono">{d.day}</td>
                  <td className="mono num">{d.visits}</td>
                  <td className="mono num">{fmtDuration(d.active_s)}</td>
                  <td className="mono num">{d.kills}</td>
                  <td className="mono num">{fmtGp(d.gold)}</td>
                  <td className="mono num">{fmtInt(d.xp)}</td>
                  <td className="mono num">{fmtNum(d.gold_per_kill, 1)}</td>
                  <td className="mono num">{d.hits_lost}</td>
                  <td className="mono num">
                    {d.deaths.pk}/{d.deaths.mob}
                  </td>
                  <td className="mono num">{d.speech_holds}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </div>
  );
}

import { fmtDuration } from "../format.ts";
import { eventView, fmtGp, fmtIngotPrice, fmtInt, fmtNum, fmtStamp, legEnd, miningKpis, oneLocalDay, planSpotName, type MiningLeg } from "../jobs.ts";
import { EventStrip } from "./Charts.tsx";
import { Badge, Panel } from "./common.tsx";
import { fetchMining, JobsHead, JobsLoading, useJobPoll, useMiningPlan, type JobDashboardProps } from "./JobsCommon.tsx";
import { MiningOptimizer } from "./MiningOptimizer.tsx";

/** The tooltip of a leg's gold: each metal's ingots and the per-ingot price the value used. */
function legPrices(l: MiningLeg): string | undefined {
  const metals = { ...l.metals_made, ...l.metals_stored };
  const lines = Object.keys(metals).map((m) => {
    const p = l.ingot_prices[m] ?? null;
    const n = `${fmtInt(l.metals_stored[m] ?? 0)} stored · ${fmtInt(l.metals_made[m] ?? 0)} made`;
    return `${m}: ${n} at ${fmtIngotPrice({ price_gp: p && p.gp, price_t: p && p.t, price_source: p && p.source })}`;
  });
  return lines.join("\n") || undefined;
}

/** Mining job dashboard from /api/jobs?job=mining (harness/jobs.py compute_mining) and the planner's
 *  /api/jobs/plan?job=mining (jobs.mining_plan). */
export function MiningJobs({ onJob, range, onRange }: JobDashboardProps) {
  const { data, error, at, reload, loading } = useJobPoll(fetchMining, range);
  const plan = useMiningPlan();
  if (!data) return <JobsLoading job="mining" onJob={onJob} error={error} />;
  const planned = plan.res?.plan ?? null;
  const t = data.totals;
  const legs = data.legs;
  const sameDay = oneLocalDay(legs.flatMap((l) => (l.t_start === null ? [] : [l.t_start])));
  const maxOre = Math.max(1, ...data.spots.map((s) => s.ore));
  const metals = t.metals ?? [];
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
        onRefresh={() => {
          reload();
          plan.reload();
        }}
        range={range}
        onRange={onRange}
        loading={loading}
      />

      <div className="kpis">
        {miningKpis(t).map((k) => (
          <div key={k.key} className={`kpi kpi-${k.tone}`} title={k.hint}>
            <span className="kpi-label">{k.label}</span>
            <span className="kpi-value">{k.value}</span>
            <span className="kpi-sub">{k.sub ?? " "}</span>
          </div>
        ))}
      </div>
      <div className="jobs-sub dim">
        Field time is the dig phase less the walk out and the travel lockout (the planner's measure, mining_opt.trip_obs). Active time is the legs
        plus the gaps of up to 2 min between them. Ore is smelted at the spot's forge when the pack nears full weight and the rest at the house's forge;
        at each trip's end the runner puts the ingots into the house's Resource Stockpile.
      </div>

      <MiningOptimizer plan={plan} />

      <div className="jobs-charts">
        <Panel title={`Spots in range (${data.spots.length})`}>
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
                    <td title={s.spot}>{planSpotName(planned, s.spot)}</td>
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
                  <th className="num">gold stored</th>
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
                      <td title={l.spot ?? undefined}>{planSpotName(planned, l.spot) ?? "—"}</td>
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
                      <td className="mono num" title={legPrices(l)}>
                        {fmtGp(l.value_gp)}
                        {l.value_unpriced_ingots > 0 && <span className="dim"> +{fmtInt(l.value_unpriced_ingots)} unpriced</span>}
                      </td>
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
                <th className="num">gold stored</th>
                <th className="num">gold / field hr</th>
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
                  <td className="mono num" title={d.value_unpriced_ingots > 0 ? `${fmtInt(d.value_unpriced_ingots)} stored ingots unpriced` : undefined}>
                    {fmtGp(d.value_gp)}
                  </td>
                  <td className="mono num" title={`${fmtGp(d.made_value_gp)} of ingots made`}>
                    {fmtGp(d.gp_per_field_hour)}
                  </td>
                  <td className="mono num">
                    {d.deaths} · {d.sent_home}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      {metals.length > 0 && (
        <Panel title={`Metals (${metals.length})`}>
          <table className="counts">
            <thead>
              <tr>
                <th>metal</th>
                <th className="num">ingots made</th>
                <th className="num">ingots stored</th>
                <th>price now</th>
                <th className="num">made at the legs' prices</th>
                <th className="num">stored at the legs' prices</th>
              </tr>
            </thead>
            <tbody>
              {metals.map((m) => (
                <tr key={m.name}>
                  <td className="mono">{m.name}</td>
                  <td className="mono num">{fmtInt(m.made)}</td>
                  <td className="mono num">{fmtInt(m.stored)}</td>
                  <td className={m.price_gp === null ? "dim" : "mono"}>{fmtIngotPrice(m)}</td>
                  <td className="mono num">{fmtGp(m.made_gp)}</td>
                  <td className="mono num">{fmtGp(m.stored_gp)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="dim">
            Prices are the store's ingot:&lt;metal&gt; rows; each leg uses the newest one at or before its end. A metal without any row stays unpriced.
          </p>
        </Panel>
      )}
    </div>
  );
}

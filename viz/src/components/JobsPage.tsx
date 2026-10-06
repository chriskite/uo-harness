import { useCallback, useEffect, useState } from "react";
import { fetchJobs, fetchLumberPlan } from "../api.ts";
import { fmtDuration } from "../format.ts";
import {
  eventView,
  fmtCounts,
  fmtGp,
  fmtPrice,
  fmtNum,
  fmtStamp,
  kpis,
  legText,
  oneLocalDay,
  phaseList,
  splitShares,
  spotRows,
  tripHome,
  woodShares,
  type JobsResponse,
  type PlanResponse,
  type RangeBounds,
} from "../jobs.ts";
import { EventStrip, LogsPerTripChart, RateChart, TimeSplitChart } from "./Charts.tsx";
import { Badge, Panel, Skeleton } from "./common.tsx";
import { HuntJobs } from "./HuntJobs.tsx";
import { JobsHead, JobsLoading, REFRESH_MS, useJobPoll, type JobDashboardProps, type JobKind } from "./JobsCommon.tsx";

const fetchLumber = (tz: number, range: RangeBounds) => fetchJobs("lumber", tz, range);

/** The plan poll: the last answer (null until the first), the last error, refresh now. */
interface PlanState {
  res: PlanResponse | null;
  error: string | null;
  reload: () => void;
}

/** The optimizer's plan, polled on its own (seconds when the server's per-minute cache is
 *  cold) so the rest of the dashboard never waits for it. */
function usePlan(): PlanState {
  const [res, setRes] = useState<PlanResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    fetchLumberPlan().then(
      (r) => {
        setRes(r);
        setError(null);
      },
      (e: unknown) => setError(String(e)),
    );
  }, []);
  useEffect(() => {
    reload();
    const t = setInterval(reload, REFRESH_MS);
    return () => clearInterval(t);
  }, [reload]);
  return { res, error, reload };
}

/** The Jobs page: one dashboard per job, picked by the switch in its head, over the head's date range. */
export function JobsPage({ job, ...props }: JobDashboardProps & { job: JobKind }) {
  return job === "hunt" ? <HuntJobs {...props} /> : <LumberJobs {...props} />;
}

/** Lumber job dashboard from /api/jobs?job=lumber (harness/jobs.py compute). */
function LumberJobs({ onJob, range, onRange }: JobDashboardProps) {
  const { data, error, at, reload, loading } = useJobPoll(fetchLumber, range);
  const plan = usePlan();
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
            Lumber job <span className="dim">· {t.trips ? `${fmtStamp(t.first_t!)} → ${fmtStamp(t.last_t!)}` : data.since > 0 || data.until !== null ? "no trips in this range" : "no trips recorded"}</span>
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
            {data.harvest.nothing_near ? ` · ${data.harvest.nothing_near} trees out of wood (nothing nearby)` : ""}
            {data.harvest.success_rate !== null && ` (${Math.round(data.harvest.success_rate * 100)}% land)`}
          </>
        )}
      </div>

      <OptimizerPanels data={data} plan={plan} />

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
        <Panel title="Field rate per trip">
          <RateChart
            points={trips.flatMap((r) => (r.t_end === null ? [] : [{ t: r.t_end, n: r.n }]))}
            series={[
              { label: "field logs/hr", cls: "field", values: trips.filter((r) => r.t_end !== null).map((r) => r.field_logs_per_hour), dots: true },
              { label: "whole-trip logs/hr", cls: "cum", values: trips.filter((r) => r.t_end !== null).map((r) => r.logs_per_hour) },
            ]}
            what="trip"
            unit="logs/hr"
            note="field = the optimizer's λ sample (no travel, lockout or banking)"
          />
        </Panel>
      </div>

      <div className="jobs-charts">
        <Panel title="Where trip time goes">
          <TimeSplitChart trips={trips} />
        </Panel>
        <Panel title="Logs per trip">
          <LogsPerTripChart trips={trips} />
        </Panel>
      </div>

      <div className="jobs-charts">
        <Panel title={`Deaths, thefts, PKs, flees (${data.events.length})`}>
          <EventStrip spans={trips.map((r) => ({ n: r.n, t_start: r.t_start, t_end: r.t_end, label: `trip #${r.n} · ${r.logs} logs` }))} events={data.events.filter((e) => e.kind !== "travel")} />
          {data.events.length === 0 ? (
            <p className="dim">No deaths, thefts, PK sightings or flees recorded.</p>
          ) : (
            <ol className="job-events">
              {data.events.filter((e) => e.kind !== "travel").reverse().map((e, i) => {
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
                  <span className="mono num dim" title={`at each trip's price; now ${fmtPrice(w.price)}`}>
                    {fmtGp(w.total_gp)}
                  </span>
                </div>
              ))}
            </div>
          )}
          <p className="dim small">
            {data.woods_file
              ? `${data.woods.length} woods, ${priced.length} with a price (ctl lumber price board:<wood>; else woods.json)`
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
                  <th>spot</th>
                  <th>outcome</th>
                  <th className="num">time</th>
                  <th className="num">logs</th>
                  <th className="num" title="boards put away at home: the chest, the bank (older trips) or the stockpile">put away</th>
                  <th className="num" title="boards the Resource Stockpile confirmed taking">stored</th>
                  <th className="num">logs/hr</th>
                  <th className="num">field/hr</th>
                  <th className="num">chops</th>
                  <th className="num">captchas</th>
                  <th>split (travel · lockout · field · rest)</th>
                  <th>travel legs</th>
                  <th className="num">skill</th>
                  <th>events</th>
                  <th className="num">value</th>
                </tr>
              </thead>
              <tbody>
                {[...trips].reverse().map((r) => (
                  <tr key={r.n}>
                    <td className="mono">{r.n}</td>
                    <td className="mono">{r.t_start ? fmtStamp(r.t_start, sameDay) : "—"}</td>
                    <td className="mono">{r.spot ?? "—"}</td>
                    <td>
                      <Badge kind={tripHome(r.outcome) ? "ok" : r.place_fail ? "bad" : "warn"} title={r.why ?? undefined}>
                        {r.outcome}
                      </Badge>
                      {r.why && <span className="dim small"> {r.why.length > 48 ? `${r.why.slice(0, 48)}…` : r.why}</span>}
                    </td>
                    <td className="mono num">{r.duration_s === null ? "—" : fmtDuration(r.duration_s)}</td>
                    <td className="mono num">{r.logs}</td>
                    <td className="mono num">{r.stored}</td>
                    <td className="mono num">{r.stockpiled}</td>
                    <td className="mono num">{fmtNum(r.logs_per_hour, 0)}</td>
                    <td className="mono num">{fmtNum(r.field_logs_per_hour, 0)}</td>
                    <td className="mono num" title="successful chops / attempts">
                      {r.successes}/{r.attempts}
                    </td>
                    <td className={r.captchas ? "mono num warn" : "mono num"} title={r.captchas ? `${fmtNum(r.captcha_wait_s)} s waiting` : undefined}>
                      {r.captchas}
                    </td>
                    <td className="mono dim" title={phaseList(r.phases_s).map(([k, s]) => `${k} ${fmtDuration(s)}`).join(", ")}>
                      {r.time_split
                        ? [r.time_split.travel, r.time_split.lockout, r.time_split.field, r.time_split.other].map((s) => fmtDuration(s)).join(" · ")
                        : "—"}
                    </td>
                    <td>
                      <span className="legs">
                        {r.travel.length === 0 && <span className="dim">—</span>}
                        {r.travel.map((l, i) => {
                          const v = legText(l);
                          return (
                            <Badge key={i} kind={v.tone} title={`${l.book ?? ""}${l.charges !== undefined ? ` · ${l.charges} charges before` : ""}${l.s !== undefined ? ` · ${fmtDuration(l.s)}` : ""}`}>
                              {v.text}
                            </Badge>
                          );
                        })}
                      </span>
                    </td>
                    <td className="mono num" title={r.skill_end !== null && r.skill !== null ? `${r.skill} → ${r.skill_end}` : undefined}>
                      {fmtNum(r.skill_end ?? r.skill, 1)}
                    </td>
                    <td>
                      {Object.entries(r.events).map(([k, n]) => (
                        <Badge key={k} kind={k === "death" ? "bad" : "warn"}>
                          {k} {n}
                        </Badge>
                      ))}
                    </td>
                    <td
                      className="mono num"
                      title={Object.entries(r.board_prices).map(([w, p]) => `${w}: ${fmtPrice(p)}`).join("\n") || undefined}
                    >
                      {fmtGp(r.value_gp)}
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
                <th className="num">trips</th>
                <th className="num">logs</th>
                <th className="num" title="boards the Resource Stockpile confirmed taking">stored</th>
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
                  <td className="mono num">{d.stockpiled}</td>
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

/** What the self-optimizing lumber job (harness/lumber_opt.py) decides and learns from:
 *  the next pick, the per-spot model, skill over time, travel legs, tomes and supplies.
 *  The first two come from the plan, which loads on its own: skeletons until it answers. */
function OptimizerPanels({ data, plan: planState }: { data: JobsResponse; plan: PlanState }) {
  const pending = planState.res === null;
  const plan = planState.res?.plan ?? null;
  const { rows, hidden } = spotRows(plan);
  const pick = plan?.pick ?? null;
  const sup = data.supplies;
  const shares = splitShares(data.time_split);
  const splitTotal = shares.reduce((a, s) => a + s.s, 0);
  return (
    <>
      <Panel
        title="Optimizer: next pick"
        extra={
          <span className="dim small">
            ctl lumber plan over all history (not the date range), recomputed each minute
            {planState.error && !pending && <span className="error"> · refresh failed: {planState.error}</span>}
          </span>
        }
      >
        {pending ? (
          planState.error ? (
            <p className="error">{planState.error}</p>
          ) : (
            <Skeleton widths={["60%", "92%", "78%"]} label="computing the optimizer plan" />
          )
        ) : !plan ? (
          <p className="dim">No plan: no Codex yet.</p>
        ) : (
          <>
            <div className="plan-pick">
              {pick ? (
                <>
                  <span>
                    <b className="mono">{pick.spot}</b>{" "}
                    <Badge kind={pick.mode === "exploit" ? "ok" : "info"} title={`best by posterior mean: ${pick.greedy}`}>
                      {pick.mode}
                    </Badge>
                  </span>
                  <span>P(best) {Math.round(pick.p_best * 100)}%</span>
                  <span>
                    {pick.trips} trip{pick.trips === 1 ? "" : "s"} of {pick.logs_per_trip} logs (Q*)
                  </span>
                  <span>~{fmtNum(pick.expected_trip_min, 0)} min/trip</span>
                  {pick.landing && (
                    <span title={`${pick.landing.source === "library" ? `${pick.landing.library} library` : "own book"}; walk in ${pick.landing.route_tiles ?? "?"} tiles`}>
                      out by <b>{pick.landing.name}</b> ({pick.landing.dist} tiles off)
                    </span>
                  )}
                  <span title="renewal-reward: a death stores nothing, a trip sent home stores what it carries">
                    ~{pick.expected_stored_trip} stored/trip · P(death) {fmtNum(pick.p_death_trip * 100, 1)}% · sent home{" "}
                    {Math.round(pick.p_sent_home_trip * 100)}%
                  </span>
                  <span>expected {pick.expected_net_logs_h} net logs/hr</span>
                </>
              ) : (
                <Badge kind="bad">{plan.error ?? "no pick"}</Badge>
              )}
            </div>
            <p className="dim small">
              skill {fmtNum(plan.skill, 1)} (chop success {plan.success_p === null ? "—" : `${Math.round(plan.success_p * 100)}%`}) · regrowth{" "}
              {plan.regrow.minutes} min ({plan.regrow.fitted ? `fitted on ${plan.regrow.pairs} retried trees` : "default"}) · P(death | PK seen){" "}
              {Math.round(plan.death_given_sighting * 100)}% · creature deaths {fmtNum(plan.creature_deaths_per_h, 3)}/hr · thefts{" "}
              {fmtNum(plan.thefts_pooled_per_h, 3)}/hr ({plan.theft_events} seen, {Math.round(plan.theft_fraction * 100)}% of the load each) · gear at
              risk{" "}
              <span title={plan.gear_at_risk.items.map((i) => `${i.n} × ${i.item}: ${i.gp} gp`).join("\n") || undefined}>
                {plan.gear_at_risk.young ? "none (Young)" : fmtGp(plan.gear_at_risk.gp)}
                {plan.gear_at_risk.unpriced.length ? ` + ${plan.gear_at_risk.unpriced.length} unpriced` : ""}
              </span>
              {plan.capacity_logs !== null && <> · room for {plan.capacity_logs} logs</>} · new-spot prior {plan.prior_rate_logs_h} logs/hr (CV{" "}
              {plan.prior_cv}) · dispersion {plan.dispersion}
              {pick && (
                <>
                  {" · "}
                  <code>{pick.command}</code>
                </>
              )}
            </p>
          </>
        )}
      </Panel>

      <Panel
        title={pending ? "Spots" : `Spots (${rows.length})`}
        extra={hidden > 0 ? <span className="dim small">+{hidden} untried candidates or disabled (ctl lumber spots)</span> : undefined}
      >
        {pending ? (
          planState.error ? (
            <p className="dim">No plan to rank the spots by.</p>
          ) : (
            <Skeleton widths={["100%", "100%", "100%", "100%", "100%"]} label="loading the spot model" />
          )
        ) : rows.length === 0 ? (
          <p className="dim">No active spots.</p>
        ) : (
          <div className="table-wrap">
            <table className="counts trips-table">
              <thead>
                <tr>
                  <th>spot</th>
                  <th>status</th>
                  <th title="the landing rune a trip recalls to from home (nearest the grove)">reached by</th>
                  <th className="num">trips</th>
                  <th className="num">field h</th>
                  <th className="num" title="posterior mean field rate, 80% interval, rescaled to today's skill">field logs/hr</th>
                  <th className="num" title="logs stored at home per hour, overhead, deaths and supplies included">net logs/hr</th>
                  <th className="num">Q*</th>
                  <th className="num" title="per trip: room exit, recall out, walk in, lockout, recall home, room, convert, store">overhead</th>
                  <th className="num" title="hostile players sighted per field hour">PKs/hr</th>
                  <th className="num" title="h_D: deaths (PK or creature) per field hour; deaths here in the tooltip">deaths/hr</th>
                  <th className="num" title="h_S: trips a threat ended early without killing us (recall, guard flight, creature stop), per field hour">
                    sent home/hr
                  </th>
                  <th className="num" title="h_T: thefts per field hour (pooled heavily: rare)">thefts/hr</th>
                  <th className="num" title="expected logs lost per trip of Q* to death and thieves, plus the gear a death loses (in logs); P(death) per trip">
                    loss/trip
                  </th>
                  <th className="num">PK escapes</th>
                  <th className="num">creature recalls</th>
                  <th className="num">place fails</th>
                  <th className="num">supplies/trip</th>
                  <th>last trip</th>
                  <th className="num">P(best)</th>
                  <th>why not</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.id} className={pick?.spot === s.id ? "spot-picked" : s.eligible ? undefined : "spot-out"}>
                    <td className="mono" title={s.name ?? undefined}>
                      {s.id}
                    </td>
                    <td>
                      <Badge kind={s.status === "active" ? (s.eligible ? "ok" : "warn") : "dim"}>{s.status}</Badge>
                    </td>
                    <td className="dim">{s.reach ?? "—"}</td>
                    <td className="mono num">{s.trips}</td>
                    <td className="mono num">{fmtNum(s.field_h, 2)}</td>
                    <td className="mono num">
                      {s.rate_logs_h} <span className="dim">({s.rate_80[0]}–{s.rate_80[1]})</span>
                    </td>
                    <td className="mono num">{s.net_logs_h}</td>
                    <td className="mono num">{s.logs_per_trip}</td>
                    <td className="mono num">{fmtDuration(s.overhead_s)}</td>
                    <td className="mono num" title={`${s.sightings} sighting(s) in trips`}>
                      {fmtNum(s.sightings_per_h, 2)}
                    </td>
                    <td className={s.deaths ? "mono num bad" : "mono num"} title={`${s.deaths} death(s) here`}>
                      {fmtNum(s.deaths_per_h, 3)}
                    </td>
                    <td className={s.sent_home ? "mono num warn" : "mono num"} title={`${s.sent_home} trip(s) sent home`}>
                      {fmtNum(s.sent_home_per_h, 2)}
                    </td>
                    <td className={s.thefts ? "mono num warn" : "mono num"} title={`${s.thefts} theft(s) here`}>
                      {fmtNum(s.thefts_per_h, 3)}
                    </td>
                    <td className="mono num" title={`P(death) per trip ${fmtNum(s.p_death_trip * 100, 1)}%`}>
                      {fmtNum(s.loss_logs_trip, 0)}
                    </td>
                    <td className={s.pk_escapes ? "mono num warn" : "mono num"}>{s.pk_escapes}</td>
                    <td className={s.creature_recalls ? "mono num warn" : "mono num"}
                        title={`${s.creature_hits ?? 0} creature hit(s) taken here`}>{s.creature_recalls ?? 0}</td>
                    <td className={s.place_fails ? "mono num warn" : "mono num"}>{s.place_fails}</td>
                    <td className="mono num" title={s.supply_unpriced ? `${s.supply_unpriced} supply units unpriced (ctl lumber price)` : undefined}>
                      {s.supply_gp_trip ? fmtGp(s.supply_gp_trip) : s.supply_unpriced ? "unpriced" : "—"}
                    </td>
                    <td title={s.last_why ?? undefined}>
                      {s.last_trip_h_ago === null ? (
                        <span className="dim">never</span>
                      ) : (
                        <>
                          <span className="mono">{fmtNum(s.last_trip_h_ago, 1)} h ago</span>{" "}
                          <Badge kind={tripHome(s.last_outcome) ? "ok" : "warn"}>{s.last_outcome ?? "?"}</Badge>
                        </>
                      )}
                    </td>
                    <td className="mono num">{s.eligible ? `${Math.round(s.p_best * 100)}%` : "—"}</td>
                    <td className="dim small">{s.why_not ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <div className="jobs-charts">
        <Panel title="Lumberjacking skill">
          <RateChart
            points={data.skill.map((p) => ({ t: p.t, n: p.n }))}
            series={[{ label: "skill at trip start / end", cls: "skill", values: data.skill.map((p) => p.skill), dots: true }]}
            what="trip"
            unit="skill"
            note="from the trip rows (start; end since 2026-10-03). Harvest Aspect: not observable yet"
          />
        </Panel>
        <Panel title="Travel and supplies">
          <div className="split-bar" title={shares.map((s) => `${s.key} ${fmtDuration(s.s)}`).join(", ")}>
            {shares.map((s) => (
              <div key={s.key} className={`seg-${s.key}`} style={{ width: `${s.share * 100}%` }} />
            ))}
          </div>
          <p className="dim small">
            all trips: {shares.map((s) => `${s.key} ${splitTotal ? Math.round(s.share * 100) : 0}%`).join(" · ")} · supplies over {sup.trips} trip(s):{" "}
            {sup.library_charges} library charges, {sup.own_charges} own charges, {sup.recall_casts} recall casts, reagents {fmtCounts(sup.reagents_used)}
            {sup.gp !== undefined && ` · ${fmtGp(sup.gp)}${sup.unpriced ? ` (+${sup.unpriced} unpriced)` : ""}`}
          </p>
          {data.travel.legs.length === 0 ? (
            <p className="dim">No recalls recorded yet.</p>
          ) : (
            <table className="counts">
              <thead>
                <tr>
                  <th>leg</th>
                  <th className="num">n</th>
                  <th className="num">landed</th>
                  <th className="num">casts</th>
                  <th className="num">charge / spell</th>
                  <th className="num">mean</th>
                  <th className="num">walk to library</th>
                  <th>failures</th>
                </tr>
              </thead>
              <tbody>
                {data.travel.legs.map((l) => (
                  <tr key={l.leg}>
                    <td>{l.leg}</td>
                    <td className="mono num">{l.n}</td>
                    <td className={l.ok < l.n ? "mono num warn" : "mono num"}>{l.ok}</td>
                    <td className="mono num">{l.casts}</td>
                    <td className="mono num">
                      {l.charge} / {l.spell}
                    </td>
                    <td className="mono num">{l.mean_s === null ? "—" : fmtDuration(l.mean_s)}</td>
                    <td className="mono num">{l.mean_walk_s === null ? "—" : fmtDuration(l.mean_walk_s)}</td>
                    <td className="dim small">{fmtCounts(l.failures)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {data.travel.books.length > 0 && (
            <table className="counts">
              <thead>
                <tr>
                  <th>book</th>
                  <th>whose</th>
                  <th>runes used</th>
                  <th className="num">uses</th>
                  <th className="num" title="charges the book showed before the newest recall">charges</th>
                  <th className="num">lowest seen</th>
                  <th>last seen</th>
                </tr>
              </thead>
              <tbody>
                {data.travel.books.map((b) => (
                  <tr key={b.book}>
                    <td className="mono">{b.book}</td>
                    <td className="dim">{b.own ? "ours" : `library${b.library ? ` (${b.library})` : ""}`}</td>
                    <td className="mono">{b.runes.join(", ") || "—"}</td>
                    <td className="mono num">{b.uses}</td>
                    <td className="mono num" title={b.charges.map(([t, c]) => `${fmtStamp(t)}: ${c}`).join("\n")}>
                      {b.last_charges ?? "—"}
                    </td>
                    <td className="mono num">{b.min_charges ?? "—"}</td>
                    <td className="mono">{b.last_t ? fmtStamp(b.last_t) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>
    </>
  );
}

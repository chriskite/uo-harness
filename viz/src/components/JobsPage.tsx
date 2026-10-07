import { useCallback, useEffect, useState } from "react";
import { fetchJobs, fetchLumberPlan } from "../api.ts";
import { fmtDuration } from "../format.ts";
import {
  EVENT_HELP,
  eventName,
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
          <div key={k.key} className={`kpi kpi-${k.tone}`} title={k.hint}>
            <span className={k.hint ? "kpi-label hint" : "kpi-label"}>{k.label}</span>
            <span className="kpi-value">{k.value}</span>
            <span className="kpi-sub">{k.sub ?? " "}</span>
          </div>
        ))}
      </div>
      <div className="jobs-sub dim">
        <span className="hint" title="Each trip's logs at the board price of their wood as of the trip's end (ctl lumber price board:<wood>); a wood with no recorded price uses woods.json">
          estimated value
        </span>{" "}
        <span className="mono">{fmtGp(t.value_gp)}</span>
        {t.value_unpriced_logs > 0 && ` (${t.value_unpriced_logs} logs unpriced${data.woods_file ? "" : ": no harness/data/woods.json"})`}
        {data.harvest && (
          <>
            {" · "}
            <span
              className="hint"
              title={
                "Every chop attempt by its result:\n" +
                "success: the chop gave logs\n" +
                "fail: no logs this time (the tree still has wood)\n" +
                "depleted: the tree is out of wood until it regrows\n" +
                "unreachable: no way to get within reach of the tree\n" +
                "not a tree: the target wasn't harvestable\n" +
                "nothing nearby: a Smart Harvest stand found no tree with wood in reach; the trees around it are marked out of wood\n" +
                "% land: success / (success + fail)"
              }
            >
              chop attempts
            </span>
            : {data.harvest.success} success / {data.harvest.fail} fail / {data.harvest.depleted} depleted /{" "}
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
              {
                label: `rolling ${windowMin} min`, cls: "roll", values: data.rolling.map((p) => p.logs_per_hour), dots: true,
                hint: `At each trip's end: logs per active hour over the trips that ended in the last ${windowMin} min of wall time`,
              },
              {
                label: "cumulative", cls: "cum", values: data.rolling.map((p) => (p.logs_per_hour === null ? null : p.cum_logs_per_hour)),
                hint: "At each trip's end: all logs so far over all active time so far",
              },
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
              {
                label: "field logs/hr", cls: "field", values: trips.filter((r) => r.t_end !== null).map((r) => r.field_logs_per_hour), dots: true,
                hint: "Logs per hour at the grove only (chopping and walking between stands): no travel, lockout, room or storing. This is the field rate λ the optimizer learns per spot",
              },
              {
                label: "whole-trip logs/hr", cls: "cum", values: trips.filter((r) => r.t_end !== null).map((r) => r.logs_per_hour),
                hint: "The trip's logs over its whole duration, travel and home time included",
              },
            ]}
            what="trip"
            unit="logs/hr"
            note="field = time at the trees only; the gap to whole-trip is the overhead"
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
                    <Badge kind={v.tone} title={EVENT_HELP[e.kind]}>
                      {v.label}
                    </Badge>
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
                  <th title="Trip number in this range, oldest = 1">#</th>
                  <th>start</th>
                  <th title="The lumber spot (tree area) the trip went to">spot</th>
                  <th title="stored: back home with the boards put away; aborted: the trip ended early (why beside it; red: the place couldn't be worked); banked: an old trip that ended at a bank">
                    outcome
                  </th>
                  <th className="num" title="Trip duration, start to end">time</th>
                  <th className="num" title="Logs chopped on the trip">logs</th>
                  <th className="num" title="Boards put away at home: the chest, the bank (older trips) or the stockpile">put away</th>
                  <th className="num" title="Boards the Resource Stockpile confirmed taking">stored</th>
                  <th className="num" title="Logs per hour over the whole trip, travel and home time included">logs/hr</th>
                  <th className="num" title="Logs per field hour: time at the grove only (chopping and walking between stands); blank under 1 min of field time">
                    field/hr
                  </th>
                  <th className="num" title="Chops that gave logs / chop attempts">chops</th>
                  <th className="num" title="Real captchas met (time waiting on hover)">captchas</th>
                  <th title="Where the time went. travel: recall casts + the walk to a library tome; lockout: the 60 s harvest lockout after travel; field: at the grove; rest: room, walks, convert, store. Each phase's time on hover">
                    split (travel · lockout · field · rest)
                  </th>
                  <th title="Each recall: out (home → landing), home (back) or escape (away from a threat). ✓ landed, ✗ failed; 'N casts' when it took retries, with why in brackets. Book, charges and seconds on hover">
                    travel legs
                  </th>
                  <th className="num" title="Lumberjacking at the trip's end (start → end on hover)">skill</th>
                  <th title="Job events during the trip, by kind (meaning on hover)">events</th>
                  <th className="num" title="The trip's logs at the board price of their wood as of the trip's end (prices used on hover)">value</th>
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
                        <Badge key={k} kind={k === "death" ? "bad" : "warn"} title={EVENT_HELP[k]}>
                          {eventName(k)} {n}
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
                <th className="num" title="Trips started that day">trips</th>
                <th className="num">logs</th>
                <th className="num" title="Boards the Resource Stockpile confirmed taking">stored</th>
                <th className="num" title="The sum of the day's trip durations">active</th>
                <th className="num" title="Logs per active hour">logs/hr</th>
                <th className="num">logs/trip</th>
                <th className="num">captchas</th>
                <th className="num" title="Deaths to players / deaths to creatures">deaths pk/mob</th>
                <th className="num" title="Suspected thefts (logs, boards and items lost)">thefts</th>
                <th className="num" title="The day's logs at the board prices of each trip's end">value</th>
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
                    <Badge
                      kind={pick.mode === "exploit" ? "ok" : "info"}
                      title={
                        `exploit: the pick is also the best spot by posterior mean. explore: Thompson sampling drew a less certain spot, ` +
                        `to learn more about it. Best by posterior mean: ${pick.greedy}`
                      }
                    >
                      {pick.mode}
                    </Badge>
                  </span>
                  <span className="hint" title="The share of the Monte Carlo draws (one posterior sample per spot each) in which this spot had the best net logs/hr">
                    P(best) {Math.round(pick.p_best * 100)}%
                  </span>
                  <span
                    className="hint"
                    title={
                      "The run to start: this many trips (about an hour's worth), each carrying Q* logs home. " +
                      "Q* is the trip size that maximises net logs/hr: longer trips spread the overhead, " +
                      "but carry more to lose to a death, a thief or a threat that sends us home (≈ λ·√(2T/h) for a small death hazard)"
                    }
                  >
                    {pick.trips} trip{pick.trips === 1 ? "" : "s"} of {pick.logs_per_trip} logs (Q*)
                  </span>
                  <span className="hint" title="Expected minutes per trip at Q*: the overhead plus the field time to chop Q* logs">
                    ~{fmtNum(pick.expected_trip_min, 0)} min/trip
                  </span>
                  {pick.landing && (
                    <span
                      className="hint"
                      title={`The rune the trip recalls to from home, the one nearest the grove: ${pick.landing.source === "library" ? `${pick.landing.library} library` : "our own book"}; walk in ${pick.landing.route_tiles ?? "?"} tiles`}
                    >
                      out by <b>{pick.landing.name}</b> ({pick.landing.dist} tiles off)
                    </span>
                  )}
                  <span
                    className="hint"
                    title="Per trip at Q*: expected logs stored at home (a death stores nothing, a trip sent home stores what it carries), the chance the trip ends in a death, and the chance a threat sends us home early"
                  >
                    ~{pick.expected_stored_trip} stored/trip · P(death) {fmtNum(pick.p_death_trip * 100, 1)}% · sent home{" "}
                    {Math.round(pick.p_sent_home_trip * 100)}%
                  </span>
                  <span className="hint" title="Expected logs stored at home per hour of agent time at Q*: overhead, deaths (logs, gear and 20 min recovery), thefts and supplies included">
                    expected {pick.expected_net_logs_h} net logs/hr
                  </span>
                </>
              ) : (
                <Badge kind="bad">{plan.error ?? "no pick"}</Badge>
              )}
            </div>
            <p className="dim small">
              <span className="hint" title="Lumberjacking from the newest trip row; chop success = the chance one chop gives logs at that skill and hatchet bonus (woods.json formulas)">
                skill {fmtNum(plan.skill, 1)} (chop success {plan.success_p === null ? "—" : `${Math.round(plan.success_p * 100)}%`})
              </span>
              {" · "}
              <span
                className="hint"
                title="How long a tree out of wood is left alone before it's tried again: where the chance it has regrown reaches 60%, fitted on depleted trees tried again later (else the 45 min default)"
              >
                regrowth {plan.regrow.minutes} min ({plan.regrow.fitted ? `fitted on ${plan.regrow.pairs} retried trees` : "default"})
              </span>
              {" · "}
              <span className="hint" title="Pooled over all spots: the chance a hostile-player sighting ends in our death">
                P(death | PK seen) {Math.round(plan.death_given_sighting * 100)}%
              </span>
              {" · "}
              <span className="hint" title="Pooled over all spots: deaths to creatures per field hour">
                creature deaths {fmtNum(plan.creature_deaths_per_h, 3)}/hr
              </span>
              {" · "}
              <span className="hint" title="Pooled over all spots: thefts per field hour, how many were seen, and the expected share of the carried logs one theft takes">
                thefts {fmtNum(plan.thefts_pooled_per_h, 3)}/hr ({plan.theft_events} seen, {Math.round(plan.theft_fraction * 100)}% of the load each)
              </span>
              {" · "}
              <span
                className="hint"
                title={
                  "What a death would cost in gear we carry, at recorded prices (a Young character loses nothing)" +
                  (plan.gear_at_risk.items.length ? `:\n${plan.gear_at_risk.items.map((i) => `${i.n} × ${i.item}: ${i.gp} gp`).join("\n")}` : "")
                }
              >
                gear at risk {plan.gear_at_risk.young ? "none (Young)" : fmtGp(plan.gear_at_risk.gp)}
                {plan.gear_at_risk.unpriced.length ? ` + ${plan.gear_at_risk.unpriced.length} unpriced` : ""}
              </span>
              {plan.capacity_logs !== null && (
                <>
                  {" · "}
                  <span className="hint" title="Logs the pack can still take before the weight limit (0.025 stone each); Q* is capped by it">
                    room for {plan.capacity_logs} logs
                  </span>
                </>
              )}
              {" · "}
              <span
                className="hint"
                title="The field rate an untried spot starts from: a spot like the measured ones (their spread). CV is its relative uncertainty, at least 0.35, so new spots get explored but not trusted"
              >
                new-spot prior {plan.prior_rate_logs_h} logs/hr (CV {plan.prior_cv})
              </span>
              {" · "}
              <span
                className="hint"
                title="φ: how much more per-trip log counts scatter than a Poisson count would (logs come about 8 to a successful chop); at least 8. It widens every spot's rate interval"
              >
                dispersion {plan.dispersion}
              </span>
              {pick && (
                <>
                  {" · "}
                  <code className="hint" title="The runner command the overseer would start for this pick">
                    {pick.command}
                  </code>
                </>
              )}
            </p>
          </>
        )}
      </Panel>

      <Panel
        title={pending ? "Spots" : `Spots (${rows.length})`}
        extra={
          <span className="dim small">
            <span className="hint" title="The highlighted row is the next pick; dimmed rows can't be picked now (see why not)">
              highlighted = next pick · dimmed = not eligible
            </span>
            {hidden > 0 && <> · +{hidden} untried candidates or disabled (ctl lumber spots)</>}
          </span>
        }
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
                  <th title="Spot id (its name on hover)">spot</th>
                  <th title="active (green: can be picked now; amber: not right now, see why not), candidate (proposed, untried) or disabled">status</th>
                  <th title="The landing rune a trip recalls to from home (nearest the grove): its name, library or own book, and tiles from the grove">
                    reached by
                  </th>
                  <th className="num" title="Trips recorded here (all history)">trips</th>
                  <th
                    className="num"
                    title="Field hours here, summed over all trips: time at the grove only (the harvest phase minus the walk out and the 60 s travel lockout). The evidence behind the field rate"
                  >
                    field h
                  </th>
                  <th
                    className="num"
                    title="λ, logs per field hour: the posterior mean and its 80% interval. Trips count less as they age (half-life 14 days) and are rescaled to today's chop success"
                  >
                    field logs/hr
                  </th>
                  <th
                    className="num"
                    title="Expected logs stored at home per hour of agent time at Q*: field time, overhead, deaths (carried logs, gear and 20 min recovery), thefts and supply cost included. The pick maximises this"
                  >
                    net logs/hr
                  </th>
                  <th
                    className="num"
                    title="Q*: the logs per trip the planner would carry home before recalling. Longer trips spread the overhead but carry more to lose; Q* maximises net logs/hr, capped by what the pack can hold"
                  >
                    Q*
                  </th>
                  <th
                    className="num"
                    title="T: mean time per trip spent not chopping here (recency-weighted): room exit, walk to the landing's rune, recall out, walk into the grove, lockout, recall home, into the room, convert, store"
                  >
                    overhead
                  </th>
                  <th className="num" title="Hostile players sighted per field hour here (posterior mean; sightings in trips on hover)">PKs/hr</th>
                  <th className="num" title="h_D: deaths (player or creature) per field hour, posterior mean shrunk toward the pooled rate; deaths here on hover">
                    deaths/hr
                  </th>
                  <th
                    className="num"
                    title="h_S: trips a threat ended early without killing us (recall away, guard flight, a creature stop) per field hour; the carried logs still come home"
                  >
                    sent home/hr
                  </th>
                  <th className="num" title="h_T: thefts per field hour (rare, so it leans heavily on the pooled rate); thefts here on hover">thefts/hr</th>
                  <th
                    className="num"
                    title="Expected logs lost per trip of Q*: to a death (what we carry), to thieves, plus the gear a death loses converted to logs. P(death) per trip on hover"
                  >
                    loss/trip
                  </th>
                  <th className="num" title="Recalls and guard flights away from players here (all history)">PK escapes</th>
                  <th className="num" title="Recalls away from a creature here; creature hits taken here on hover">creature recalls</th>
                  <th
                    className="num"
                    title="Aborted trips with no logs because the place couldn't be worked (no reachable tree, or harvesting answered with something unknown). Two in a row keep the spot out for 7 days"
                  >
                    place fails
                  </th>
                  <th
                    className="num"
                    title={
                      "Mean gp of the supplies one trip here used, recent trips weighted more (half-life 14 days): " +
                      "reagents at reagent:<name>, our own runebook's charges at recall_charge, trapped pouches set off at trapped_pouch. " +
                      "Public library tome charges are free. Items with no recorded price count 0 and show as unpriced (ctl lumber price)"
                    }
                  >
                    supplies/trip
                  </th>
                  <th title="Hours since the last trip here and how it ended (why on hover)">last trip</th>
                  <th className="num" title="The share of the Monte Carlo draws (one posterior sample per spot each) in which this spot had the best net logs/hr">
                    P(best)
                  </th>
                  <th title="Why the spot can't be picked now: its status, a cooldown (30 min after a death or player threat here, 20 min after a thief made us leave), place failures, no landing, …">
                    why not
                  </th>
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
            series={[
              {
                label: "skill at trip start / end", cls: "skill", values: data.skill.map((p) => p.skill), dots: true,
                hint: "Lumberjacking as each trip row recorded it, at its start and (since 2026-10-03) its end",
              },
            ]}
            what="trip"
            unit="skill"
            note="from the trip rows (start; end since 2026-10-03). Harvest Aspect: not observable yet"
          />
        </Panel>
        <Panel title="Travel and supplies">
          <div
            className="split-bar"
            title={
              "All trips' time: travel (recall casts + the walk to a library tome), lockout (the 60 s harvest lockout after travel), " +
              "field (at the grove), other (room, walks, convert, store). " +
              shares.map((s) => `${s.key} ${fmtDuration(s.s)}`).join(", ")
            }
          >
            {shares.map((s) => (
              <div key={s.key} className={`seg-${s.key}`} style={{ width: `${s.share * 100}%` }} />
            ))}
          </div>
          <p className="dim small">
            all trips: {shares.map((s) => `${s.key} ${splitTotal ? Math.round(s.share * 100) : 0}%`).join(" · ")} ·{" "}
            <span
              className="hint"
              title={
                "Summed over the trips whose rows record supplies. Library charges: from public library tomes (free). " +
                "Own charges: from our runebook (priced at recall_charge). Recall casts: the Recall spell (reagents). " +
                "Reagents: what left the pack. The gp total uses the recorded prices; unpriced units are counted, not guessed"
              }
            >
              supplies over {sup.trips} trip(s)
            </span>
            : {sup.library_charges} library charges, {sup.own_charges} own charges, {sup.recall_casts} recall casts, reagents {fmtCounts(sup.reagents_used)}
            {sup.gp !== undefined && ` · ${fmtGp(sup.gp)}${sup.unpriced ? ` (+${sup.unpriced} unpriced)` : ""}`}
          </p>
          {data.travel.legs.length === 0 ? (
            <p className="dim">No recalls recorded yet.</p>
          ) : (
            <table className="counts">
              <thead>
                <tr>
                  <th title="out: from home to the grove's landing; home: back; escape: away from a threat">leg</th>
                  <th className="num" title="Legs of this kind">n</th>
                  <th className="num" title="Legs that arrived where they meant to">landed</th>
                  <th className="num" title="Recall attempts (a failed cast is retried)">casts</th>
                  <th className="num" title="Casts from a book's charge / by the Recall spell">charge / spell</th>
                  <th className="num" title="Mean seconds per leg, from the start to the arrival (the walk to a library tome included)">mean</th>
                  <th className="num" title="Mean walk from where we stood to the library tome, for legs that used one">walk to library</th>
                  <th title="Failed casts by reason">failures</th>
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
                  <th title="The book's serial">book</th>
                  <th title="ours, or a public rune library's tome">whose</th>
                  <th title="Runes recalled to from this book (Witcher numbers for library tomes)">runes used</th>
                  <th className="num" title="Recalls from this book">uses</th>
                  <th className="num" title="Charges the book showed before the newest recall (history on hover)">charges</th>
                  <th className="num" title="The fewest charges it ever showed">lowest seen</th>
                  <th title="When it was last used">last seen</th>
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

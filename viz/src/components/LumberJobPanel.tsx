// The left-column Lumber job pane: shown while a lumber run is going (App.tsx, runningLumber).
// The spot and this trip's per-wood logs come from the runner's intent; the spot's expected
// rates and its past trips from /api/jobs/plan and /api/jobs?job=lumber (all time).
import { useEffect, useState } from "react";
import { fmtDuration } from "../format.ts";
import { intentClock } from "../intent.ts";
import { fmtNum, fmtStamp, tripHome, type DateRange } from "../jobs.ts";
import { currentRate, lumberLeg, RATE_MIN_S, spotTrips, sumWoods, woodMix } from "../lumberjob.ts";
import type { VizSnapshot } from "../store.ts";
import type { AgentIntent } from "../types.ts";
import { Badge, Panel, Skeleton } from "./common.tsx";
import { fetchLumber, useJobPoll, usePlan } from "./JobsCommon.tsx";

const ALL_TIME: DateRange = { from: null, to: null };
const HISTORY_ROWS = 5;

const LEG_BADGE = {
  out: <Badge kind="info">heading to</Badge>,
  grove: <Badge kind="ok">at</Badge>,
  home: <Badge kind="dim">back from</Badge>,
} as const;

export function LumberJobPanel({ intent, viz }: { intent: AgentIntent; viz: VizSnapshot }) {
  const [wallNow, setWallNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setWallNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);
  const intents = viz.state?.intents;
  const { data, error } = useJobPoll(fetchLumber, ALL_TIME);
  const plan = usePlan();
  const spot = intent.spot ? (plan.res?.plan?.spots.find((s) => s.id === intent.spot) ?? null) : null;
  const here = intent.spot && data ? spotTrips(data.trips, intent.spot) : [];
  const pastMix = woodMix(sumWoods(here));
  const mix = woodMix(intent.woods);
  const total = mix.reduce((s, w) => s + w.logs, 0);
  const legKey = lumberLeg(intent, intents);
  const leg = LEG_BADGE[legKey];
  const rate = currentRate(intent, intents, intentClock(viz.state, viz.events, wallNow));

  return (
    <Panel
      title="Lumber job"
      className="lumber-job"
      extra={
        typeof intent.trip === "number" ? (
          <span className="mono dim">
            trip {intent.trip}/{intent.trips ?? "?"}
          </span>
        ) : undefined
      }
    >
      <div className="pad">
        {intent.spot ? (
          <>
            <div className="lj-spot">
              {leg}
              <b>{spot?.name ?? intent.spot}</b>
              {spot?.name && <span className="mono dim">{intent.spot}</span>}
              {spot?.pvp && <Badge kind="warn">PvP</Badge>}
              {spot && spot.status !== "active" && <Badge kind="dim">{spot.status}</Badge>}
            </div>
            {spot?.reach && <div className="dim small">via {spot.reach}</div>}
          </>
        ) : (
          <div className="lj-spot">
            {leg}
            <span className="dim">spot unknown (the proxy or runner predates the spot field)</span>
          </div>
        )}

        {intent.spot && (
          <>
            <h3>Expected</h3>
            {spot ? (
              <div className="kv-grid">
                <span>field logs/hr</span>
                <span className="mono">
                  {fmtNum(spot.rate_logs_h, 0)} (80% {fmtNum(spot.rate_80[0], 0)}–{fmtNum(spot.rate_80[1], 0)})
                </span>
                <span>net logs/hr</span>
                <span className="mono">{fmtNum(spot.net_logs_h, 0)}</span>
                <span>P(death)/trip</span>
                <span className="mono">{fmtNum(spot.p_death_trip * 100, 1)}%</span>
                <span>deaths/hr</span>
                <span className={spot.deaths > 0 ? "mono bad" : "mono"}>{fmtNum(spot.deaths_per_h, 3)}</span>
                <span>PKs seen/hr</span>
                <span className="mono">{fmtNum(spot.sightings_per_h, 2)}</span>
                <span>sent home/hr</span>
                <span className="mono">{fmtNum(spot.sent_home_per_h, 2)}</span>
                <span>thefts/hr</span>
                <span className="mono">{fmtNum(spot.thefts_per_h, 3)}</span>
              </div>
            ) : plan.res ? (
              <span className="dim">no plan row for {intent.spot}</span>
            ) : plan.error ? (
              <span className="warn">{plan.error}</span>
            ) : (
              <Skeleton widths={["70%", "55%", "60%"]} label="loading spot stats" />
            )}
          </>
        )}

        <h3>This trip</h3>
        {intent.woods !== undefined && (
          <div className="lj-rate" title="Logs gained per hour over the last 5 min at the grove (shorter right after arriving)">
            <span>now</span>
            {rate ? (
              <>
                <b className="mono">{fmtNum(rate.logsH, 0)}</b>
                <span>logs/hr</span>
                <span className="dim small">
                  over {fmtDuration(rate.spanS)}
                  {spot ? ` · expected ${fmtNum(spot.rate_logs_h, 0)}` : ""}
                </span>
              </>
            ) : (
              <span className="dim">
                {legKey !== "grove" || intent.kind === "lockout" ? "— not chopping" : `— measuring (needs ${RATE_MIN_S} s at the grove)`}
              </span>
            )}
          </div>
        )}
        {intent.woods === undefined ? (
          <span className="dim">no per-wood tally (the proxy or runner predates it)</span>
        ) : mix.length === 0 ? (
          <span className="dim">no logs yet</span>
        ) : (
          <>
            <div className="woods">
              {mix.map((w) => {
                const past = pastMix.find((p) => p.name === w.name);
                return (
                  <div key={w.name} className="wood-row">
                    <span>{w.name}</span>
                    <div className="bar-track">
                      <div className="bar-fill wood-fill" style={{ width: `${w.share * 100}%` }} />
                    </div>
                    <span className="mono num">{w.logs}</span>
                    <span className="mono num dim" title="share of this wood in past trips here">
                      {past ? `${Math.round(past.share * 100)}% here` : "—"}
                    </span>
                  </div>
                );
              })}
            </div>
            <div className="dim small">{total} logs</div>
          </>
        )}

        {intent.spot && (
          <>
            <h3>History here</h3>
            {spot && (
              <div className="kv-grid">
                <span>trips</span>
                <span className="mono">{spot.trips}</span>
                <span>field hours</span>
                <span className="mono">{fmtNum(spot.field_h, 1)}</span>
                <span>logs</span>
                <span className="mono">{spot.logs}</span>
                <span>deaths</span>
                <span className={spot.deaths > 0 ? "mono bad" : "mono"}>{spot.deaths}</span>
                <span>last trip</span>
                <span className="mono">
                  {fmtNum(spot.last_trip_h_ago, 1)} h ago{spot.last_outcome ? ` · ${spot.last_outcome}` : ""}
                </span>
              </div>
            )}
            {data ? (
              here.length === 0 ? (
                <span className="dim">no trips here yet</span>
              ) : (
                <>
                  <table className="counts">
                    <thead>
                      <tr>
                        <th>when</th>
                        <th className="num">logs</th>
                        <th className="num" title="Logs per field hour: time at the grove only">
                          field/hr
                        </th>
                        <th>outcome</th>
                      </tr>
                    </thead>
                    <tbody>
                      {here.slice(0, HISTORY_ROWS).map((r) => (
                        <tr key={r.n}>
                          <td className="mono">{r.t_start ? fmtStamp(r.t_start) : "—"}</td>
                          <td className="mono num">{r.logs}</td>
                          <td className="mono num">{fmtNum(r.field_logs_per_hour, 0)}</td>
                          <td>
                            <Badge kind={tripHome(r.outcome) ? "ok" : r.place_fail ? "bad" : "warn"} title={r.why ?? undefined}>
                              {r.outcome}
                            </Badge>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {pastMix.length > 0 && (
                    <div className="dim small">wood mix: {pastMix.map((w) => `${w.name} ${Math.round(w.share * 100)}%`).join(" · ")}</div>
                  )}
                </>
              )
            ) : error ? (
              <span className="warn">{error}</span>
            ) : (
              <Skeleton widths={["80%", "65%", "75%"]} label="loading trips here" />
            )}
          </>
        )}
      </div>
    </Panel>
  );
}

// The left-column Mining job pane: shown while a mining run is going (App.tsx, runningMining).
// The spot, the phase and the leg's ore so far come from the runner's intent; this trip's finished
// legs, the leg in progress's smelts (open_leg), the spot's past legs and today's stored ingots from
// /api/jobs?job=mining (all time); the spot's name, landing, expected rates and model history from the
// planner's /api/jobs/plan?job=mining (jobs.mining_plan); what is carried from the live snapshot.
import { useEffect, useState } from "react";
import { fmtDuration } from "../format.ts";
import { intentClock } from "../intent.ts";
import { fmtInt, fmtNum, fmtStamp, legEnd, localDay, oreToIngots, planSpotName, type DateRange, type Tone } from "../jobs.ts";
import { currentOreRate, FIELD_PHASES, legOre, metalMix, miningCarried, miningPhase, RATE_MIN_S, spotLegs, tripLegs, tripTally, type MiningPhase } from "../miningjob.ts";
import type { VizSnapshot } from "../store.ts";
import type { AgentIntent } from "../types.ts";
import { Badge, Panel, Skeleton } from "./common.tsx";
import { fetchMining, useJobPoll, useMiningPlan } from "./JobsCommon.tsx";

const ALL_TIME: DateRange = { from: null, to: null };
const HISTORY_ROWS = 5;

const PHASE_BADGE: Record<MiningPhase, { label: string; tone: Tone }> = {
  out: { label: "heading out", tone: "info" },
  dig: { label: "digging", tone: "ok" },
  smelt: { label: "smelting", tone: "ok" },
  lockout: { label: "lockout", tone: "warn" },
  escape: { label: "escaping", tone: "bad" },
  hop: { label: "hopping", tone: "info" },
  home: { label: "home", tone: "dim" },
};

export function MiningJobPanel({ intent, viz }: { intent: AgentIntent; viz: VizSnapshot }) {
  const [wallNow, setWallNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setWallNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);
  const intents = viz.state?.intents;
  const { data, error } = useJobPoll(fetchMining, ALL_TIME);
  const phase = miningPhase(intent, intents);
  const badge = intent.kind === "to_forge" ? { label: "to the forge", tone: "ok" as const } : PHASE_BADGE[phase];
  const rate = currentOreRate(intent, intents, intentClock(viz.state, viz.events, wallNow));
  const done = data ? tripLegs(data.legs, intent, intents) : [];
  const ended = intent.kind === "trip_done";
  const legStart = done.length ? done[done.length - 1]!.t_end : null;
  const current = ended ? null : { ore: legOre(intent, intents, legStart) };
  const tally = tripTally(done, current, data?.open_leg);
  const here = intent.spot ? data?.spots.find((s) => s.spot === intent.spot) : undefined;
  const carried = miningCarried(viz.state?.world);
  const storedToday = data?.days.find((d) => d.day === localDay(wallNow))?.ingots_stored ?? 0;
  const target = intent.target ? `${intent.target[0]},${intent.target[1]}` : null;
  const plan = useMiningPlan();
  const planned = plan.res?.plan ?? null;
  const spot = intent.spot ? (planned?.spots.find((s) => s.id === intent.spot) ?? null) : null;
  const hereLegs = intent.spot && data ? spotLegs(data.legs, intent.spot) : [];
  const mix = metalMix(hereLegs);
  /** A legs-table spot cell: the plan's name (truncated by .ml-spot), the name and id on hover. */
  const spotCell = (id: string | null | undefined) => {
    const name = planSpotName(planned, id);
    return { name: name ?? "—", title: id ? (name !== id ? `${name} · ${id}` : id) : undefined };
  };
  const nowCell = spotCell(intent.spot);

  return (
    <Panel
      title="Mining job"
      className="mining-job"
      extra={
        typeof intent.trip === "number" ? (
          <span className="mono dim">
            trip {intent.trip}/{intent.trips ?? "?"}
          </span>
        ) : undefined
      }
    >
      <div className="pad">
        <div className="lj-spot">
          <Badge kind={badge.tone} title={intent.kind}>
            {badge.label}
          </Badge>
          {intent.spot ? (
            <>
              <b>{spot?.name ?? intent.spot}</b>
              {spot?.name && <span className="mono dim">{intent.spot}</span>}
              {spot?.pvp && <Badge kind="warn">PvP</Badge>}
              {spot && spot.status !== "active" && <Badge kind="dim">{spot.status}</Badge>}
            </>
          ) : (
            <span className="dim">spot unknown</span>
          )}
          {target && <span className="mono dim">{target}</span>}
        </div>
        {spot?.reach && <div className="dim small">via {spot.reach}</div>}

        {intent.spot && (
          <>
            <h3>Expected</h3>
            {spot ? (
              <div className="kv-grid">
                <span>field ingots/hr</span>
                <span className="mono" title="The planner's field rate λ (posterior mean, 80% interval) as ingots: 2 an ore">
                  {fmtNum(oreToIngots(spot.rate_ore_h), 0)} (80% {fmtNum(oreToIngots(spot.rate_80[0]), 0)}–{fmtNum(oreToIngots(spot.rate_80[1]), 0)})
                </span>
                <span>field ore/hr</span>
                <span className="mono">{fmtNum(spot.rate_ore_h, 0)}</span>
                <span>net ingots/hr</span>
                <span className="mono" title="The planner's score: the field rate less the cost of threats that send us home and of deaths">
                  {fmtNum(oreToIngots(spot.score_ore_h), 0)}
                </span>
                <span>P(best)</span>
                <span className="mono">{spot.eligible && spot.p_best !== null ? `${Math.round(spot.p_best * 100)}%` : `— (${spot.why_not ?? "not eligible"})`}</span>
                <span>P(death)/run</span>
                <span className="mono">{fmtNum(spot.p_death_stint * 100, 1)}%</span>
                <span>deaths/hr</span>
                <span className={spot.deaths > 0 ? "mono bad" : "mono"}>{fmtNum(spot.deaths_per_h, 3)}</span>
                <span>sent home/hr</span>
                <span className="mono">{fmtNum(spot.sent_home_per_h, 2)}</span>
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
        <div className="lj-rate" title="Ore dug per hour over the last 5 min at the spot (shorter right after arriving)">
          <span>now</span>
          {rate ? (
            <>
              <b className="mono">{fmtNum(rate.oreH, 0)}</b>
              <span>ore/hr</span>
              <span className="dim small">
                over {fmtDuration(rate.spanS)}
                {spot ? ` · expected ${fmtNum(spot.rate_ore_h, 0)}` : here ? ` · spot ${fmtNum(here.ore_per_hour, 0)}` : ""}
              </span>
            </>
          ) : (
            <span className="dim">{FIELD_PHASES[phase] ? `— measuring (needs ${RATE_MIN_S} s at the spot)` : "— not digging"}</span>
          )}
        </div>
        <div className="kv-grid">
          <span>ore dug</span>
          <span className="mono">
            {data ? fmtInt(tally.ore) : "—"}
            {current && done.length > 0 ? <span className="dim"> ({current.ore} this leg)</span> : null}
          </span>
          <span>smelts · ingots made</span>
          <span className="mono" title={tally.smelts === null ? "the viz server predates open_leg: restart it for the leg in progress's smelts" : undefined}>
            {data && tally.smelts !== null ? `${tally.smelts} · ${fmtInt(tally.ingotsMade)}` : "—"}
          </span>
        </div>

        <h3>Carried now</h3>
        {carried ? (
          <div className="kv-grid">
            <span>ore</span>
            <span className="mono">{fmtInt(carried.ore)}</span>
            <span>ingots</span>
            <span className="mono">{fmtInt(carried.ingots)}</span>
            <span>pickaxes</span>
            <span className={carried.pickaxesWorn + carried.pickaxesPacked === 0 ? "mono bad" : "mono"}>
              {carried.pickaxesWorn} worn · {carried.pickaxesPacked} in the pack
            </span>
            <span>stored today</span>
            <span className="mono" title="Ingots that went into the house's resource stockpile today">
              {data ? `${fmtInt(storedToday)} ingots` : "—"}
            </span>
          </div>
        ) : (
          <span className="dim">no character in the snapshot</span>
        )}

        <h3>Legs this trip</h3>
        {data ? (
          <>
            <table className="counts">
              <thead>
                <tr>
                  <th className="num">leg</th>
                  <th>spot</th>
                  <th className="num">ore</th>
                  <th className="num" title="Ore per field hour: time at the spot less the walk out and the lockout">
                    field/hr
                  </th>
                  <th>outcome</th>
                </tr>
              </thead>
              <tbody>
                {done.map((l) => {
                  const end = legEnd(l);
                  const cell = spotCell(l.spot);
                  return (
                    <tr key={l.n}>
                      <td className="mono num">{l.leg}</td>
                      <td className="ml-spot" title={cell.title}>
                        {cell.name}
                      </td>
                      <td className="mono num">{l.ore}</td>
                      <td className="mono num">{fmtNum(l.ore_per_hour, 0)}</td>
                      <td>
                        <Badge kind={end.tone} title={l.why ?? undefined}>
                          {end.text}
                        </Badge>
                      </td>
                    </tr>
                  );
                })}
                {current && (
                  <tr>
                    <td className="mono num">{(done[done.length - 1]?.leg ?? 0) + 1}</td>
                    <td className="ml-spot" title={nowCell.title}>
                      {nowCell.name}
                    </td>
                    <td className="mono num">{current.ore}</td>
                    <td className="mono num">—</td>
                    <td>
                      <Badge kind="info">now</Badge>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </>
        ) : error ? (
          <span className="warn">{error}</span>
        ) : (
          <Skeleton widths={["80%", "65%", "75%"]} label="loading this trip's legs" />
        )}

        {intent.spot && (
          <>
            <h3>History here</h3>
            {spot && (
              <div className="kv-grid">
                <span>legs</span>
                <span className="mono">{spot.trips}</span>
                <span>field hours</span>
                <span className="mono">{fmtNum(spot.field_h, 1)}</span>
                <span>ore</span>
                <span className="mono">{fmtInt(spot.ore)}</span>
                <span>ingots stored</span>
                <span className="mono">{here ? fmtInt(here.ingots_stored) : "—"}</span>
                <span>dry legs</span>
                <span className="mono">{here?.dry ?? "—"}</span>
                <span>deaths</span>
                <span className={spot.deaths > 0 ? "mono bad" : "mono"}>{spot.deaths}</span>
                <span>last leg</span>
                <span className="mono">
                  {spot.last_trip_h_ago === null ? "never" : `${fmtNum(spot.last_trip_h_ago, 1)} h ago${spot.last_outcome ? ` · ${spot.last_outcome}` : ""}`}
                </span>
              </div>
            )}
            {data ? (
              hereLegs.length === 0 ? (
                <span className="dim">no legs here yet</span>
              ) : (
                <>
                  <table className="counts">
                    <thead>
                      <tr>
                        <th>when</th>
                        <th className="num">ore</th>
                        <th className="num" title="Ore per field hour: time at the spot less the walk out and the lockout">
                          field/hr
                        </th>
                        <th>outcome</th>
                      </tr>
                    </thead>
                    <tbody>
                      {hereLegs.slice(0, HISTORY_ROWS).map((l) => {
                        const end = legEnd(l);
                        return (
                          <tr key={l.n}>
                            <td className="mono">{l.t_start ? fmtStamp(l.t_start) : "—"}</td>
                            <td className="mono num">{l.ore}</td>
                            <td className="mono num">{fmtNum(l.ore_per_hour, 0)}</td>
                            <td>
                              <Badge kind={end.tone} title={l.why ?? undefined}>
                                {end.text}
                              </Badge>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                  {mix.length > 0 && (
                    <div className="dim small">metal mix: {mix.map((m) => `${m.name} ${Math.round(m.share * 100)}%`).join(" · ")}</div>
                  )}
                </>
              )
            ) : error ? (
              <span className="warn">{error}</span>
            ) : (
              <Skeleton widths={["80%", "65%", "75%"]} label="loading legs here" />
            )}
          </>
        )}
      </div>
    </Panel>
  );
}

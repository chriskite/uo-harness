// The left-column Mining job pane: shown while a mining run is going (App.tsx, runningMining).
// The spot, the phase and the leg's ore so far come from the runner's intent; this trip's finished
// legs, the leg in progress's smelts (open_leg), the spot's past rate and today's stored ingots from
// /api/jobs?job=mining (all time); what is carried from the live snapshot.
import { useEffect, useState } from "react";
import { fmtDuration } from "../format.ts";
import { intentClock } from "../intent.ts";
import { fmtInt, fmtNum, legEnd, localDay, type DateRange, type Tone } from "../jobs.ts";
import { currentOreRate, FIELD_PHASES, legOre, miningCarried, miningPhase, RATE_MIN_S, tripLegs, tripTally, type MiningPhase } from "../miningjob.ts";
import type { VizSnapshot } from "../store.ts";
import type { AgentIntent } from "../types.ts";
import { Badge, Panel, Skeleton } from "./common.tsx";
import { fetchMining, useJobPoll } from "./JobsCommon.tsx";

const ALL_TIME: DateRange = { from: null, to: null };

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
          {intent.spot ? <b>{intent.spot}</b> : <span className="dim">spot unknown</span>}
          {target && <span className="mono dim">{target}</span>}
        </div>

        <h3>This trip</h3>
        <div className="lj-rate" title="Ore dug per hour over the last 5 min at the spot (shorter right after arriving)">
          <span>now</span>
          {rate ? (
            <>
              <b className="mono">{fmtNum(rate.oreH, 0)}</b>
              <span>ore/hr</span>
              <span className="dim small">
                over {fmtDuration(rate.spanS)}
                {here ? ` · spot ${fmtNum(here.ore_per_hour, 0)}` : ""}
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
            <span className="mono" title="Ingots that went into the room's resource stockpile today">
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
                  return (
                    <tr key={l.n}>
                      <td className="mono num">{l.leg}</td>
                      <td className="mono ml-spot" title={l.spot ?? undefined}>
                        {l.spot ?? "—"}
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
                    <td className="mono ml-spot" title={intent.spot}>
                      {intent.spot ?? "—"}
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
            {here && (
              <div className="dim small">
                at this spot: {here.legs} legs · {fmtInt(here.ore)} ore · {fmtNum(here.ore_per_hour, 0)} ore/hr · {here.dry} dry
              </div>
            )}
          </>
        ) : error ? (
          <span className="warn">{error}</span>
        ) : (
          <Skeleton widths={["80%", "65%", "75%"]} label="loading this trip's legs" />
        )}
      </div>
    </Panel>
  );
}

// The left-column Hunt job pane: shown while a hunt run is going (App.tsx, runningHunt).
// The run's totals come from the runner's intent (`tally`, `started`); gold and kills per hour
// right now from the tally samples, and damage per second from the "-N" numbers, both kept by
// the stream fold (events.ts Aggregates.hunt); past visits from /api/jobs?job=hunt (all time).
import { useEffect, useState } from "react";
import { fmtDuration } from "../format.ts";
import { DPS_MIN_S, DPS_WINDOW_S, HUNT_ACTIVITY, RATE_MIN_S, damageRates, tallyRate, type HuntActivity } from "../huntjob.ts";
import { hpColor, hpFraction, intentClock } from "../intent.ts";
import { fmtGp, fmtInt, fmtNum, fmtStamp, oneLocalDay, type DateRange, type Tone } from "../jobs.ts";
import type { VizSnapshot } from "../store.ts";
import type { AgentIntent } from "../types.ts";
import { Badge, Panel, Skeleton } from "./common.tsx";
import { fetchHunt, useJobPoll } from "./JobsCommon.tsx";

const ALL_TIME: DateRange = { from: null, to: null };
const HISTORY_ROWS = 5;
// The intent came without `tally`: an older proxy or runner sends none, and the proxy drops a malformed one whole
// (proxy.set_intent: more than 32 counters, a non-int)
const NO_TALLY = "no run tally in the intent (an older proxy or runner, or one the proxy rejected)";

const ACTIVITY_TONE: Record<HuntActivity, Tone> = {
  fighting: "bad",
  looting: "ok",
  healing: "info",
  hunting: "ok",
  waiting: "dim",
  travelling: "info",
  banking: "dim",
};

export function HuntJobPanel({ intent, viz }: { intent: AgentIntent; viz: VizSnapshot }) {
  const [wallNow, setWallNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setWallNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);
  const now = intentClock(viz.state, viz.events, wallNow);
  const world = viz.state?.world;
  const { data, error } = useJobPoll(fetchHunt, ALL_TIME);
  const tally = intent.tally;
  const activity = HUNT_ACTIVITY[intent.kind ?? ""];
  const target = intent.target_serial ? world?.mobiles[intent.target_serial] : undefined;
  const hp = target ? hpFraction(target.hits, target.hits_max) : null;
  const intents = viz.state?.intents;
  const gold = tallyRate(viz.agg.hunt.tally, intents, intent, "gold", now);
  const kills = tallyRate(viz.agg.hunt.tally, intents, intent, "kills", now);
  const dps = damageRates(viz.agg.hunt, world, now);
  const runS = now !== null && intent.started !== undefined ? now - intent.started : null;
  const runGoldH = tally && runS !== null && runS >= RATE_MIN_S ? ((tally.gold ?? 0) / runS) * 3600 : null;
  const runKillsH = tally && runS !== null && runS >= RATE_MIN_S ? ((tally.kills ?? 0) / runS) * 3600 : null;
  const visits = data ? [...data.visits].reverse().slice(0, HISTORY_ROWS) : [];
  const sameDay = oneLocalDay(visits.flatMap((v) => (v.t_start === null ? [] : [v.t_start])));
  const measuring = tally ? `— measuring (needs ${RATE_MIN_S} s)` : `— ${NO_TALLY}`;

  return (
    <Panel
      title="Hunt job"
      className="hunt-job"
      extra={typeof intent.trip === "number" ? <span className="mono dim">visit {intent.trip}</span> : undefined}
    >
      <div className="pad">
        <div className="lj-spot">
          {activity ? <Badge kind={ACTIVITY_TONE[activity]}>{activity}</Badge> : <Badge kind="dim">{intent.kind ?? "doing"}</Badge>}
          {target ? (
            <>
              <b>{target.name ?? intent.target_serial}</b>
              {hp !== null && (
                <span className="hj-hp" title={`${Math.round(hp * 100)}% hits`}>
                  <span className="bar-track">
                    <span className="bar-fill" style={{ width: `${hp * 100}%`, background: hpColor(hp) }} />
                  </span>
                  <span className="mono dim">{Math.round(hp * 100)}%</span>
                </span>
              )}
            </>
          ) : (
            <span className="dim">no target</span>
          )}
        </div>

        <h3>Now</h3>
        <div className="lj-rate" title="Gold looted over the last 5 min, per hour (shorter at the run's start or after the page loaded)">
          {gold ? (
            <>
              <b className="mono">{fmtInt(gold.perH)}</b>
              <span>gold/hr</span>
              <span className="dim small">
                over {fmtDuration(gold.spanS)}
                {runGoldH !== null ? ` · run ${fmtInt(runGoldH)}` : ""}
              </span>
            </>
          ) : (
            <span className="dim">gold/hr {measuring}</span>
          )}
        </div>
        <div
          className="lj-rate"
          title={`Damage numbers over the last ${DPS_WINDOW_S} s, per second. Dealt: on hostile creatures in view (whoever hit them); taken: on us and our pets.`}
        >
          {dps ? (
            <>
              <b className="mono">{fmtNum(dps.dealt, 1)}</b>
              <span>dmg/s dealt</span>
              <b className={dps.taken > 0 ? "mono warn" : "mono"}>{fmtNum(dps.taken, 1)}</b>
              <span>taken</span>
              <span className="dim small">last {fmtDuration(dps.spanS)}</span>
            </>
          ) : (
            <span className="dim">dmg/s — measuring (needs {DPS_MIN_S} s)</span>
          )}
        </div>
        <div className="lj-rate" title="Kills over the last 5 min, per hour">
          {kills ? (
            <>
              <b className="mono">{fmtNum(kills.perH, 0)}</b>
              <span>kills/hr</span>
              <span className="dim small">{runKillsH !== null ? `run ${fmtNum(runKillsH, 0)}` : ""}</span>
            </>
          ) : (
            <span className="dim">kills/hr {measuring}</span>
          )}
        </div>

        <h3>This run</h3>
        {tally ? (
          <div className="kv-grid">
            <span>time</span>
            <span className="mono">{runS !== null ? fmtDuration(runS) : "—"}</span>
            <span>kills</span>
            <span className="mono">
              {tally.kills ?? 0}
              {tally.lost_kills ? <span className="dim"> (+{tally.lost_kills} others' loot)</span> : null}
            </span>
            <span>gold looted</span>
            <span className="mono">
              {fmtGp(tally.gold ?? 0)}
              {tally.banked ? <span className="dim"> · {fmtGp(tally.banked)} banked</span> : null}
            </span>
            <span>XP (est.)</span>
            <span className="mono">{fmtInt(tally.xp ?? 0)}</span>
            <span>hits lost</span>
            <span className="mono">{tally.hits_lost ?? 0}</span>
            <span>heals · potions</span>
            <span className="mono">
              {tally.heals ?? 0} · {tally.potions ?? 0}
            </span>
            <span>visits · leaves</span>
            <span className={tally.leaves ? "mono warn" : "mono"}>
              {tally.visits ?? 0} · {tally.leaves ?? 0}
            </span>
          </div>
        ) : (
          <span className="dim">{NO_TALLY}</span>
        )}

        <h3>Past visits</h3>
        {data ? (
          visits.length === 0 ? (
            <span className="dim">no visits recorded yet</span>
          ) : (
            <>
              <table className="counts">
                <thead>
                  <tr>
                    <th>when</th>
                    <th className="num">time</th>
                    <th className="num">kills</th>
                    <th className="num">gold/hr</th>
                    <th>ended</th>
                  </tr>
                </thead>
                <tbody>
                  {visits.map((v) => (
                    <tr key={v.n}>
                      <td className="mono">{v.t_start ? fmtStamp(v.t_start, sameDay) : "—"}</td>
                      <td className="mono num">{v.duration_s === null ? "—" : fmtDuration(v.duration_s)}</td>
                      <td className="mono num">{v.kills}</td>
                      <td className="mono num" title={fmtGp(v.gold)}>
                        {fmtInt(v.gold_per_hour)}
                      </td>
                      <td className="dim ended" title={v.ended ?? undefined}>
                        {v.ended ?? "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="dim small">
                all hunts: {fmtInt(data.totals.gold_per_hour)} gold/hr · {fmtNum(data.totals.kills_per_hour, 0)} kills/hr ·{" "}
                {fmtNum(data.totals.gold_per_kill, 0)} gold/kill
              </div>
            </>
          )
        ) : error ? (
          <span className="warn">{error}</span>
        ) : (
          <Skeleton widths={["80%", "65%", "75%"]} label="loading past visits" />
        )}
      </div>
    </Panel>
  );
}

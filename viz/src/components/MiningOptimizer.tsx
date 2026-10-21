import { fmtNum, oreToIngots, outcomeKind, planSpotName, spotRows, type MiningPlanResponse } from "../jobs.ts";
import { Badge, Panel, Skeleton } from "./common.tsx";
import type { PlanState } from "./JobsCommon.tsx";

/** What the mining planner (harness/mining_opt.py, `ctl mining plan`) decides and learns from: the next
 *  pick and the per-spot model. The plan loads on its own: skeletons until it answers. The planner
 *  works in ore; these panels show ingots (INGOTS_PER_ORE a smelted ore), ore in the tooltips. */
export function MiningOptimizer({ plan: planState }: { plan: PlanState<MiningPlanResponse> }) {
  const pending = planState.res === null;
  const plan = planState.res?.plan ?? null;
  const { rows, hidden } = spotRows(plan);
  const pick = plan?.pick ?? null;
  const greedyName = planSpotName(plan, plan?.greedy?.spot) ?? "?";
  return (
    <>
      <Panel
        title="Optimizer: next pick"
        extra={
          <span className="dim small">
            ctl mining plan over all history (not the date range), recomputed each minute
            {planState.error && !pending && <span className="error"> · refresh failed: {planState.error}</span>}
          </span>
        }
      >
        {pending ? (
          planState.error ? (
            <p className="error">{planState.error}</p>
          ) : (
            <Skeleton widths={["60%", "92%", "78%"]} label="computing the mining plan" />
          )
        ) : !plan ? (
          <p className="dim">No plan: no Codex yet.</p>
        ) : (
          <>
            <div className="plan-pick">
              {pick ? (
                <>
                  <span>
                    <b>{planSpotName(plan, pick.spot)}</b> <span className="mono dim">{pick.spot}</span>{" "}
                    <Badge
                      kind={pick.mode === "exploit" ? "ok" : "info"}
                      title={
                        `exploit: the pick is also the best spot by posterior mean. explore: Thompson sampling drew a less certain spot, ` +
                        `to learn more about it. Best by posterior mean: ${greedyName}`
                      }
                    >
                      {pick.mode}
                    </Badge>
                  </span>
                  <span className="hint" title="The share of the Monte Carlo draws (one posterior sample per spot each) in which this spot had the best net ore/hr">
                    P(best) {Math.round(pick.p_best * 100)}%
                  </span>
                  <span className="hint" title="The run to start: one trip digging this many field minutes (the runner's --field-min)">
                    {pick.field_min} field min
                  </span>
                  {pick.landing && (
                    <span
                      className="hint"
                      title={`The rune the run recalls to from home, the one nearest the spot: ${pick.landing.source === "library" ? `${pick.landing.library} library` : "our own book"}`}
                    >
                      out by <b>{pick.landing.name}</b> ({pick.landing.dist} tiles off)
                    </span>
                  )}
                  <span className="hint" title={`λ: ingots per field hour here, posterior mean (${pick.rate_ore_h} ore, 2 ingots an ore)`}>
                    ~{oreToIngots(pick.rate_ore_h)} field ingots/hr ({pick.rate_ore_h} ore)
                  </span>
                  <span
                    className="hint"
                    title={`The chance the run ends in a death over its ${pick.field_min} field minutes (h_D ${fmtNum(pick.death_h, 3)}/hr), and how often per field hour a threat sends us home`}
                  >
                    P(death) {fmtNum(pick.p_death_stint * 100, 1)}% per run · sent home {pick.sent_home_h}/hr
                  </span>
                  <span
                    className="hint"
                    title="The planner's score: the field rate less the cost of trips a threat sends home (0.1 h each) and of deaths (half a run's ingots plus 0.5 h); the pick is the best posterior draw (Thompson sampling)"
                  >
                    expected {oreToIngots(pick.expected_ore_h)} net ingots/hr
                  </span>
                </>
              ) : (
                <Badge kind="bad">{plan.error ?? "no pick"}</Badge>
              )}
            </div>
            <p className="dim small">
              <span className="hint" title="Mining from the newest leg row (its skill at the leg's end)">
                skill {fmtNum(plan.skill, 1)}
              </span>
              {" · "}
              <span className="hint" title="A spot that ran dry waits this long before it can be picked again (ore nodes respawn in 30-60 min)">
                regrowth {plan.regrow_min} min
              </span>
              {" · "}
              <span className="hint" title="Pooled over all spots: deaths per field hour (the prior each spot's death rate shrinks toward)">
                deaths {plan.deaths_pooled_per_h}/hr pooled
              </span>
              {" · "}
              <span className="hint" title="Pooled over all spots: legs a threat sent home per field hour (the prior each spot's rate shrinks toward)">
                sent home {plan.sent_home_pooled_per_h}/hr pooled
              </span>
              {" · "}
              <span
                className="hint"
                title={`The field rate an untried spot starts from: the pooled rate of the measured legs (at least 3), else the first capture's (${plan.prior_ore_h} ore/hr). CV is its relative uncertainty`}
              >
                new-spot prior {oreToIngots(plan.prior_ore_h)} ingots/hr (CV {plan.prior_cv})
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
        title={pending ? "Optimizer: spots" : `Optimizer: spots (${rows.length})`}
        extra={
          <span className="dim small">
            <span className="hint" title="The highlighted row is the next pick; dimmed rows can't be picked now (see why not)">
              highlighted = next pick · dimmed = not eligible
            </span>
            {hidden > 0 && <> · +{hidden} candidates or disabled spots without legs (ctl mining spots)</>}
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
                  <th title="The spot's name (its id on hover)">spot</th>
                  <th title="active (green: can be picked now; amber: not right now, see why not), candidate or disabled">status</th>
                  <th title="The landing rune a run recalls to from home (nearest the spot): its name, library or own book, and tiles from the spot">
                    reached by
                  </th>
                  <th className="num" title="Legs recorded here (all history)">legs</th>
                  <th
                    className="num"
                    title="Field hours here, summed over all legs: the harvest phase minus the walk out and the 60 s travel lockout. The evidence behind the field rate"
                  >
                    field h
                  </th>
                  <th
                    className="num"
                    title="λ as ingots per field hour (2 × the planner's ore): the posterior mean and its 80% interval. Legs count less as they age (half-life 14 days)"
                  >
                    field ingots/hr
                  </th>
                  <th
                    className="num"
                    title="The planner's score in ingots (2 × its ore): the field rate less the cost of legs a threat sends home (0.1 h each) and of deaths (half a run's ingots plus 0.5 h). The greedy pick maximises this"
                  >
                    net ingots/hr
                  </th>
                  <th className="num" title="Ore nodes around the spot's forge (harness/ore_nodes.py)">nodes</th>
                  <th className="num" title="h_D: deaths per field hour, posterior mean shrunk toward the pooled rate; deaths here on hover">
                    deaths/hr
                  </th>
                  <th
                    className="num"
                    title="h_S: legs a threat ended early without killing us (recall away, guard flight) per field hour, posterior mean; the carried ingots still come home"
                  >
                    sent home/hr
                  </th>
                  <th className="num" title="The chance one run (its field minutes) here ends in a death, from h_D">
                    P(death)/run
                  </th>
                  <th className="num" title="Recalls and guard flights away from players here (all history)">PK escapes</th>
                  <th className="num" title="Recalls away from a creature here; creature hits taken here on hover">creature recalls</th>
                  <th title="Hours since the last leg here and how it ended (why on hover)">last leg</th>
                  <th className="num" title="The share of the Monte Carlo draws (one posterior sample per spot each) in which this spot had the best net ore/hr">
                    P(best)
                  </th>
                  <th title="Why the spot can't be picked now: its status, a no-go area, no landing, a cooldown (30 min after a threat or death here, 60 min after it ran dry)">
                    why not
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.id} className={pick?.spot === s.id ? "spot-picked" : s.eligible ? undefined : "spot-out"}>
                    <td title={s.id}>{s.name ?? s.id}</td>
                    <td>
                      <Badge kind={s.status === "active" ? (s.eligible ? "ok" : "warn") : "dim"}>{s.status}</Badge>
                    </td>
                    <td className="dim">{s.reach ?? "—"}</td>
                    <td className="mono num">{s.trips}</td>
                    <td className="mono num">{fmtNum(s.field_h, 2)}</td>
                    <td className="mono num" title={`${s.rate_ore_h} ore/hr (${s.rate_80[0]}–${s.rate_80[1]})`}>
                      {oreToIngots(s.rate_ore_h)}{" "}
                      <span className="dim">
                        ({oreToIngots(s.rate_80[0])}–{oreToIngots(s.rate_80[1])})
                      </span>
                    </td>
                    <td className="mono num" title={`${s.score_ore_h} ore/hr`}>
                      {oreToIngots(s.score_ore_h)}
                    </td>
                    <td className="mono num">{s.nodes ?? "—"}</td>
                    <td className={s.deaths ? "mono num bad" : "mono num"} title={`${s.deaths} death(s) here`}>
                      {fmtNum(s.deaths_per_h, 3)}
                    </td>
                    <td className={s.sent_home ? "mono num warn" : "mono num"} title={`${s.sent_home} leg(s) sent home`}>
                      {fmtNum(s.sent_home_per_h, 2)}
                    </td>
                    <td className="mono num">{fmtNum(s.p_death_stint * 100, 1)}%</td>
                    <td className={s.pk_escapes ? "mono num warn" : "mono num"}>{s.pk_escapes}</td>
                    <td className={s.creature_recalls ? "mono num warn" : "mono num"} title={`${s.creature_hits} creature hit(s) taken here`}>
                      {s.creature_recalls}
                    </td>
                    <td title={s.last_why ?? undefined}>
                      {s.last_trip_h_ago === null ? (
                        <span className="dim">never</span>
                      ) : (
                        <>
                          <span className="mono">{fmtNum(s.last_trip_h_ago, 1)} h ago</span>{" "}
                          <Badge kind={outcomeKind(s.last_outcome)}>{s.last_outcome ?? "?"}</Badge>
                        </>
                      )}
                    </td>
                    <td className="mono num">{s.eligible && s.p_best !== null ? `${Math.round(s.p_best * 100)}%` : "—"}</td>
                    <td className="dim small">
                      {s.why_not ?? ""}
                      {s.ready_in_min !== null && ` (ready in ${s.ready_in_min} min)`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}

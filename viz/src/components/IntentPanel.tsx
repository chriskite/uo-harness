import { useEffect, useState } from "react";
import { intentClock, intentView, recentIntents } from "../intent.ts";
import type { VizSnapshot } from "../store.ts";
import { Badge, Panel } from "./common.tsx";

/** What the agent runner says it is doing right now ("Heading to tree at …"),
 * with a live activity indicator, and the steps before it ("6s ago  Chopping …"). */
export function IntentPanel({ viz }: { viz: VizSnapshot }) {
  const [wallNow, setWallNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setWallNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);

  const now = intentClock(viz.state, viz.events, wallNow);
  const v = intentView(viz.state?.intent, now);
  const recent = recentIntents(viz.state?.intents, now);
  return (
    <Panel
      title="Agent"
      extra={v?.context ? <span className="mono dim">{v.context}</span> : undefined}
      className={v && v.activity !== "idle" ? `intent busy-${v.activity}` : "intent"}
    >
      {!v ? (
        <span className="dim">no agent intent reported this session</span>
      ) : (
        <>
          <div className="intent-now">
            <span className={`intent-activity activity-${v.activity}`} aria-label={v.activity} />
            <span className="intent-text">{v.text}</span>
          </div>
          <div className="intent-meta">
            <Badge kind={v.tone}>{v.kind}</Badge>
            {v.target && <span className="mono">→ {v.target}</span>}
            {v.age && <span className="mono dim">{v.age}</span>}
          </div>
        </>
      )}
      {recent.length > 0 && (
        <ol className="intent-history">
          {recent.map((r) => (
            <li key={r.key} className={`intent-past tone-${r.tone}`}>
              <span className="mono dim ago">{r.ago ?? ""}</span>
              <span className="past-text" title={r.text}>
                {r.text}
              </span>
              <span className="mono dim took" title="how long this step took">
                {r.took}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}

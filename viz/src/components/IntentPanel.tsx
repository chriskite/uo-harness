import { useEffect, useState } from "react";
import { intentClock, intentView } from "../intent.ts";
import type { VizSnapshot } from "../store.ts";
import { Badge, Panel } from "./common.tsx";

/** What the agent runner says it is doing right now ("Heading to tree at …"). */
export function IntentPanel({ viz }: { viz: VizSnapshot }) {
  const [wallNow, setWallNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setWallNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);

  const v = intentView(viz.state?.intent, intentClock(viz.state, viz.events, wallNow));
  return (
    <Panel title="Agent" extra={v?.context ? <span className="mono dim">{v.context}</span> : undefined} className="intent">
      {!v ? (
        <span className="dim">no agent intent reported this session</span>
      ) : (
        <>
          <div className="intent-text">{v.text}</div>
          <div className="intent-meta">
            <Badge kind={v.tone}>{v.kind}</Badge>
            {v.target && <span className="mono">→ {v.target}</span>}
            {v.age && <span className="mono dim">{v.age}</span>}
          </div>
        </>
      )}
    </Panel>
  );
}

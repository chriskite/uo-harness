// The Sim page's runner decisions: the runner's intents and log lines from a minute before t to 10 s
// after, the last one at or before t highlighted and kept in view. A click seeks.
import { useEffect, useRef } from "react";
import { describeEvent, fmtSimT } from "../sim/markers.ts";
import type { Trace } from "../sim/trace.ts";

const BEFORE_S = 60;
const AFTER_S = 10;

export function SimRunnerPanel({ trace, t, onSeek }: { trace: Trace; t: number; onSeek: (t: number) => void }) {
  const lines = trace.eventsIn(t - BEFORE_S, t + AFTER_S, (e) => e.e === "intent" || e.e === "log");
  let now = -1;
  for (let i = 0; i < lines.length && lines[i]!.t <= t; i++) now = i;
  const nowRef = useRef<HTMLLIElement>(null);
  const nowEv = lines[now];
  useEffect(() => {
    nowRef.current?.scrollIntoView({ block: "nearest" });
  }, [nowEv]);
  if (!lines.length) return <div className="dim">No runner intents or log lines within a minute of here.</div>;
  return (
    <ul className="sim-list">
      {lines.map((e, i) => (
        <li key={`${e.t}-${i}`} ref={i === now ? nowRef : undefined} className={i === now ? "now" : i > now ? "future" : undefined} onClick={() => onSeek(e.t)}>
          <span className="t">{fmtSimT(e.t)}</span>
          {e.e === "intent" ? <span className="intent">{describeEvent(e, trace)}</span> : e.text}
        </li>
      ))}
    </ul>
  );
}

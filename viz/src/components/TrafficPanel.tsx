import { C2S_NAMES, DIR_NAMES, fmtTime } from "../format.ts";
import { trafficSummary, type AgentAction } from "../events.ts";
import { displayName } from "../serial.ts";
import type { EventData, Snapshot, TrafficCounts } from "../types.ts";
import { Panel } from "./common.tsx";

function detailText(d: EventData, world: Snapshot | null): string {
  switch (d.ev) {
    case "walk":
      return `walk ${DIR_NAMES[Number(d.dir) & 7]}${d.run ? " (run)" : ""} seq ${String(d.seq)}`;
    case "speech":
      return `say "${String(d.text ?? "")}"`;
    default: {
      const who = d.serial !== undefined ? displayName(world, d.serial) : null;
      return who ? `${d.ev} ${who}` : d.ev;
    }
  }
}

/** Counts come from the state's cumulative `traffic` (whole session); the last
 * agent action comes from the event stream. */
export function TrafficPanel({
  traffic,
  lastAgent: last,
  world,
}: {
  traffic: TrafficCounts | undefined;
  lastAgent: AgentAction | null;
  world: Snapshot | null;
}) {
  const tr = trafficSummary(traffic ?? { proxy_events: {}, c2s: [] });
  const sources = Object.keys(tr.bySrc).sort();
  const ids = [...new Set(sources.flatMap((s) => Object.keys(tr.bySrc[s] ?? {})))].sort();

  return (
    <Panel title="Traffic">
      {!traffic ? (
        <div className="warn">state response has no traffic counters (proxy predates them)</div>
      ) : sources.length === 0 ? (
        <div className="dim">no non-client C2S packets yet (client packets are not itemized)</div>
      ) : (
        <table className="counts">
          <thead>
            <tr>
              <th>C2S id</th>
              {sources.map((s) => (
                <th key={s}>{s}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ids.map((id) => (
              <tr key={id}>
                <td className="mono">
                  {id} <span className="dim">{C2S_NAMES[id] ?? ""}</span>
                </td>
                {sources.map((s) => (
                  <td key={s} className="mono num">
                    {tr.bySrc[s]?.[id] ?? 0}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="kv-grid">
        <span>confirms hidden</span>
        <span className="mono">{tr.confirmsHidden}</span>
        <span>confirms rewritten</span>
        <span className="mono">{tr.confirmsRewritten}</span>
        <span title="fabricated S2C 0x21 sent to the client only">client re-anchors</span>
        <span className="mono">{tr.reanchors}</span>
        {Object.entries(tr.tokenEvents).map(([ev, n]) => (
          <span key={ev} className="contents">
            <span>{ev}</span>
            <span className="mono">{n}</span>
          </span>
        ))}
      </div>
      <div className="last-agent">
        <span className="dim">last agent action </span>
        {last ? (
          <span className="mono">
            {fmtTime(last.t)} [{last.src}] {last.id} {last.detail ? detailText(last.detail, world) : (C2S_NAMES[last.id] ?? "")}
          </span>
        ) : (
          <span className="dim">none</span>
        )}
      </div>
    </Panel>
  );
}

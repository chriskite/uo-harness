import { DIR_NAMES, fmtTime } from "../format.ts";
import type { Aggregates } from "../events.ts";
import type { EventData, StateResponse } from "../types.ts";
import { divergence } from "../walk.ts";
import { Badge, Panel } from "./common.tsx";

const LOG_ROWS = 14;

function describe(d: EventData): string {
  const xy = (v: unknown) => (Array.isArray(v) ? v.join(",") : "?");
  switch (d.ev) {
    case "step":
      return `${xy(d.from)} → ${xy(d.to)} z${String(d.z)}`;
    case "blocked":
      return `from ${xy(d.from)} dir ${DIR_NAMES[Number(d.dir) & 7]} (${String(d.src ?? "?")})`;
    case "reanchor_client":
      return `client → ${String(d.x)},${String(d.y)},${String(d.z)} ${DIR_NAMES[Number(d.dir) & 7]}`;
    default: {
      const parts = [d.src !== undefined ? String(d.src) : "", d.note !== undefined ? String(d.note) : ""];
      return parts.filter(Boolean).join(": ");
    }
  }
}

export function MovementPanel({ state, agg }: { state: StateResponse | null; agg: Aggregates }) {
  if (!state) return <Panel title="Movement">no state yet</Panel>;
  const m = state.movement;
  const self = state.world.self;
  const div = divergence(m, self);
  const log = agg.movementLog.slice(-LOG_ROWS).reverse();

  return (
    <Panel
      title="Movement"
      extra={
        <span className="badges">
          {m.stalled && <Badge kind="bad">STALLED</Badge>}
          {div.diverged && (
            <Badge kind="warn" title="world.self (dead reckoning) disagrees with the server-true position">
              DIVERGED
            </Badge>
          )}
          {m.client_stale && (
            <Badge kind="warn" title="client position is stale; a re-anchor is due">
              client stale
            </Badge>
          )}
          {m.resync_pending && <Badge kind="info">resync pending</Badge>}
        </span>
      }
    >
      <div className="kv-grid">
        <span>true pos</span>
        <span className="mono strong">
          {m.pos ? `${m.pos[0]},${m.pos[1]},${m.pos[2]} ${DIR_NAMES[m.pos[3] & 7]}` : "unknown"}
        </span>
        <span>world model</span>
        <span className={div.diverged ? "mono warn" : "mono"}>
          {self.x},{self.y},{self.z} {DIR_NAMES[self.direction & 7]}
          {div.diverged ? ` (Δ ${div.dx >= 0 ? "+" : ""}${div.dx},${div.dy >= 0 ? "+" : ""}${div.dy})` : ""}
        </span>
        <span>inflight</span>
        <span className="mono">{m.inflight}</span>
        <span>next_seq</span>
        <span className="mono">{m.next_seq}</span>
        <span>rejects in row</span>
        <span className={m.rejects_in_row ? "mono bad" : "mono"}>{m.rejects_in_row}</span>
      </div>
      <div className="movelog">
        <table className="evtable">
          <tbody>
            {log.map((e) => (
              <tr key={e.seq}>
                <td className="mono dim">{fmtTime(e.t)}</td>
                <td className={`ev ev-${e.data.ev}`}>{e.data.ev}</td>
                <td className="mono">{describe(e.data)}</td>
              </tr>
            ))}
            {log.length === 0 && (
              <tr>
                <td className="dim">no proxy movement events yet</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

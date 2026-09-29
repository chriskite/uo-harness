import type { Counts, StateResponse } from "../types.ts";

/** Normalize a Counts value ({name: n} or [..., n] rows) to [label, n] pairs. */
function rows(c: Counts | undefined): Array<[string, number]> {
  if (!c) return [];
  if (Array.isArray(c)) return c.map((r) => [r.slice(0, -1).join(" "), Number(r[r.length - 1])]);
  return Object.entries(c);
}

function CountTable({ title, counts }: { title: string; counts: Counts | undefined }) {
  const r = rows(counts);
  return (
    <div>
      <h4>{title}</h4>
      {r.length === 0 ? (
        <div className="dim">none</div>
      ) : (
        <table className="counts">
          <tbody>
            {r.map(([k, n]) => (
              <tr key={k}>
                <td className="mono">{k}</td>
                <td className="mono num">{n}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/** Meta-information about the world model (§1.4 diagnostics); not world state. */
export function DiagnosticsPanel({ state }: { state: StateResponse | null }) {
  const d = state?.diagnostics;
  if (!d) return <div className="dim pad">no diagnostics in the state response</div>;
  return (
    <div className="pad">
      <div className="kv-grid">
        <span>parse failures</span>
        <span className="mono">{d.parse_failures ?? "?"}</span>
        <span>world errors</span>
        <span className="mono">{d.world_errors ?? state?.world_errors ?? "?"}</span>
      </div>
      <div className="diag-cols">
        <CountTable title="packet counts (top)" counts={d.packet_counts} />
        <CountTable title="unhandled" counts={d.unhandled} />
        <CountTable title="anomalies" counts={d.anomalies} />
      </div>
    </div>
  );
}

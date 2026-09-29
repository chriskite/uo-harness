import { useState } from "react";
import type { Snapshot } from "../types.ts";
import { Badge, SerialLink } from "./common.tsx";

/** Raw layout string plus numbered text lines: exactly what the agent sees. */
export function GumpViewer({ world }: { world: Snapshot | null }) {
  const gumps = world?.gumps ?? [];
  const [index, setIndex] = useState(0);
  if (gumps.length === 0) return <div className="dim pad">no gumps seen</div>;
  const i = Math.min(index, gumps.length - 1);
  const g = gumps[i]!;

  return (
    <div className="gumps pad">
      <div className="gump-list">
        {gumps.map((gg, n) => (
          <button key={`${gg.serial}/${gg.gump_id}`} type="button" className={n === i ? "tab active" : "tab"} onClick={() => setIndex(n)}>
            {gg.gump_id} {gg.open ? "●" : "○"}
          </button>
        ))}
      </div>
      <div className="kv-grid">
        <span>serial</span>
        <span>
          <SerialLink serial={g.serial} />
        </span>
        <span>gump_id</span>
        <span className="mono">{g.gump_id}</span>
        <span>state</span>
        <span>
          {g.open ? <Badge kind="ok">open</Badge> : <Badge kind="dim">closed</Badge>} responses {g.responses}
          {g.compressed ? " · compressed" : ""} · at {g.x},{g.y}
        </span>
      </div>
      <h4>layout</h4>
      <pre className="raw">{g.layout.replaceAll("\u0000", "␀")}</pre>
      <h4>lines ({g.lines.length})</h4>
      <ol className="lines" start={0}>
        {g.lines.map((line, n) => (
          <li key={n}>
            <span className="mono">{line}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

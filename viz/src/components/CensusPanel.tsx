import { entityCaption, labelOf } from "../events.ts";
import { lookupEntity } from "../serial.ts";
import type { HexSerial, Snapshot } from "../types.ts";
import { SerialLink } from "./common.tsx";

/** world.census sorted by queries: what the client chose to look at. */
export function CensusPanel({
  world,
  streamLabels,
  selected,
}: {
  world: Snapshot | null;
  streamLabels: Record<HexSerial, string>;
  selected: HexSerial | null;
}) {
  if (!world) return <div className="dim pad">no state yet</div>;
  const rows = Object.entries(world.census).sort((a, b) => b[1].queries - a[1].queries || a[0].localeCompare(b[0]));
  return (
    <table className="counts pad">
      <thead>
        <tr>
          <th>serial</th>
          <th>entity</th>
          <th>queries</th>
          <th>sources</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([serial, c]) => {
          const ref = lookupEntity(world, serial);
          const name = ref?.kind === "mobile" ? ref.mobile.name : ref?.kind === "item" ? ref.item.name : ref?.name;
          return (
            <tr key={serial}>
              <td>
                <SerialLink serial={serial} selected={serial === selected} />
              </td>
              <td>
                {entityCaption(name, labelOf(world, streamLabels, serial)) ?? ""}{" "}
                <span className="dim">{ref ? (ref.kind === "name" ? "names only" : ref.kind) : "gone"}</span>
              </td>
              <td className="mono num">{c.queries}</td>
              <td className="mono dim">{c.sources.join(" ")}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

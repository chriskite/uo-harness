import { useMemo } from "react";
import { entityCaption, eventSerials, labelOf } from "../events.ts";
import { DIR_NAMES, NOTORIETY, fmtTime } from "../format.ts";
import { layerName } from "../containers.ts";
import { displayName, hex, lookupEntity } from "../serial.ts";
import type { VizSnapshot } from "../store.ts";
import { Badge, SerialLink } from "./common.tsx";

const RECENT_EVENTS = 12;

function fieldText(k: string, v: unknown): string {
  if (typeof v === "string") return v;
  if (typeof v !== "number") return JSON.stringify(v);
  if (k === "graphic" || k === "hue") return `${hex(v, 4)} (${v})`;
  if (k === "layer") return `${hex(v, 2)} ${layerName(v)}`;
  if (k === "direction" || k === "dir") return `${v} ${DIR_NAMES[v & 7] ?? ""}`;
  if (k === "notoriety") return `${v} ${NOTORIETY[v]?.name ?? ""}`;
  return String(v);
}

export function EntityInspector({ viz }: { viz: VizSnapshot }) {
  const world = viz.state?.world ?? null;
  const serial = viz.selected;
  const ref = useMemo(() => lookupEntity(world, serial), [world, serial]);
  const recent = useMemo(
    () => (serial ? viz.events.filter((e) => eventSerials(e.data).includes(serial)).slice(-RECENT_EVENTS).reverse() : []),
    [viz.events, serial],
  );
  if (!serial) return <div className="dim pad">Click an entity on the map, a serial in the event log, or a tree node.</div>;
  if (!world) return <div className="dim pad">no state yet</div>;

  const fields: Record<string, unknown> =
    ref?.kind === "mobile" ? { ...ref.mobile } : ref?.kind === "item" ? { ...ref.item } : {};
  delete fields.container;
  const parent = ref?.kind === "item" ? ref.item.container : undefined;
  const children = Object.entries(world.items)
    .filter(([, it]) => it.container === serial)
    .sort((a, b) => (a[1].layer ?? 0) - (b[1].layer ?? 0) || a[0].localeCompare(b[0]));
  const census = world.census[serial];
  const buffs = Object.values(world.buffs[serial] ?? {});
  const label = labelOf(world, viz.agg.labels, serial);
  const name = ref?.kind === "mobile" ? ref.mobile.name : ref?.kind === "item" ? ref.item.name : ref?.name;

  return (
    <div className="inspector pad">
      <h3>
        <span className="mono">{serial}</span> {entityCaption(name, label) ?? <span className="dim">(unnamed)</span>}{" "}
        <Badge kind="dim">{ref ? (ref.kind === "name" ? "names only" : ref.kind) : "unknown"}</Badge>
        {ref?.kind === "mobile" && ref.isSelf && <Badge kind="info">self</Badge>}
        {world.containers.includes(serial) && <Badge kind="ok">OPEN</Badge>}
      </h3>
      {label && <div className="dim">click label: "{label}"</div>}
      {parent && (
        <div>
          in container <SerialLink serial={parent} label={displayName(world, parent)} />
        </div>
      )}
      <div className="kv-grid">
        {Object.entries(fields).map(([k, v]) => (
          <span key={k} className="contents">
            <span>{k}</span>
            <span className="mono">{fieldText(k, v)}</span>
          </span>
        ))}
        {census && (
          <span className="contents">
            <span>census</span>
            <span className="mono">
              {census.queries} queries ({census.sources.join(", ")})
            </span>
          </span>
        )}
      </div>
      {buffs.length > 0 && (
        <div className="buffs">
          {buffs.map((b) => (
            <span key={b.icon_id} className="buff" title={typeof b.description === "string" ? b.description : ""}>
              {b.title || `buff ${b.icon_id}`}
            </span>
          ))}
        </div>
      )}
      {children.length > 0 && (
        <>
          <h4>contents / equipment ({children.length})</h4>
          <ul className="plain">
            {children.map(([s, it]) => (
              <li key={s}>
                <SerialLink serial={s} label={it.name} />{" "}
                <span className="mono dim">{it.graphic !== undefined ? hex(it.graphic, 4) : ""}</span>
                {it.layer !== undefined && <span className="dim"> [{layerName(it.layer)}]</span>}
                {it.amount !== undefined && it.amount > 1 && <span className="dim"> ×{it.amount}</span>}
              </li>
            ))}
          </ul>
        </>
      )}
      {recent.length > 0 && (
        <>
          <h4>recent events</h4>
          <ul className="plain mono">
            {recent.map((e) => (
              <li key={e.seq}>
                <span className="dim">{fmtTime(e.t)}</span> {e.data.ev}
                {typeof e.data.text === "string" ? ` "${e.data.text}"` : ""}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

import { memo, useMemo, useState } from "react";
import { DEFAULT_FILTER, evVocabulary, filterEvents, type EventFilter } from "../events.ts";
import { fmtTime } from "../format.ts";
import { SERIAL_FIELDS, displayName, toHex } from "../serial.ts";
import { vizStore } from "../store.ts";
import type { EventData, EventEnvelope, HexSerial, Snapshot } from "../types.ts";
import { SerialLink } from "./common.tsx";

const MAX_FIELD_CHARS = 140;

function Fields({ data, world, selected }: { data: EventData; world: Snapshot | null; selected: HexSerial | null }) {
  const parts = Object.entries(data)
    .filter(([k]) => k !== "ev")
    .map(([k, v]) => {
      const serial = SERIAL_FIELDS[k] ? toHex(v) : null;
      if (serial) {
        return (
          <span key={k} className="field">
            <span className="fk">{k}</span>=<SerialLink serial={serial} label={displayName(world, serial)} selected={serial === selected} />
          </span>
        );
      }
      let text = JSON.stringify(v) ?? String(v);
      if (k === "entries" && Array.isArray(v)) {
        text = (v as Array<{ serial?: unknown; name?: unknown }>)
          .map((e) => `${toHex(e.serial) ?? "?"}=${String(e.name)}`)
          .join(", ");
      }
      if (text.length > MAX_FIELD_CHARS) text = text.slice(0, MAX_FIELD_CHARS) + "…";
      return (
        <span key={k} className="field">
          <span className="fk">{k}</span>={text}
        </span>
      );
    });
  return <>{parts}</>;
}

const Row = memo(function Row({ env, world, selected }: { env: EventEnvelope; world: Snapshot | null; selected: HexSerial | null }) {
  return (
    <div className={`logrow origin-${env.origin}`}>
      <span className="mono dim seq">{env.seq}</span>
      <span className="mono dim">{fmtTime(env.t)}</span>
      <span className={`ev ev-${env.data.ev}`}>{env.data.ev}</span>
      <span className="fields">
        <Fields data={env.data} world={world} selected={selected} />
      </span>
    </div>
  );
});

export function EventLog({ events, world, selected }: { events: EventEnvelope[]; world: Snapshot | null; selected: HexSerial | null }) {
  const [filter, setFilter] = useState<EventFilter>(DEFAULT_FILTER);
  const [onlySelected, setOnlySelected] = useState(false);
  const [showVocab, setShowVocab] = useState(false);
  const vocab = useMemo(() => evVocabulary(events), [events]);
  const effective = useMemo(
    () => ({ ...filter, serial: onlySelected ? selected : null }),
    [filter, onlySelected, selected],
  );
  const shown = useMemo(() => filterEvents(events, effective).reverse(), [events, effective]);

  const toggle = (ev: string) => {
    const hidden = new Set(filter.hidden);
    if (hidden.has(ev)) hidden.delete(ev);
    else hidden.add(ev);
    setFilter({ ...filter, hidden });
  };

  return (
    <div className="eventlog">
      <header className="panel-head">
        <h2>
          Events <span className="dim mono">{shown.length}/{events.length}</span>
        </h2>
        <select value={filter.origin} onChange={(e) => setFilter({ ...filter, origin: e.target.value as EventFilter["origin"] })}>
          <option value="all">world + proxy</option>
          <option value="world">world</option>
          <option value="proxy">proxy</option>
        </select>
      </header>
      <div className="log-filters">
        <input
          type="search"
          placeholder="filter text…"
          value={filter.text}
          onChange={(e) => setFilter({ ...filter, text: e.target.value })}
        />
        <label title="only events mentioning the selected serial">
          <input type="checkbox" checked={onlySelected} onChange={(e) => setOnlySelected(e.target.checked)} />
          selected
        </label>
        <button type="button" className="link" onClick={() => setShowVocab(!showVocab)}>
          ev filter ({filter.hidden.size} hidden)
        </button>
        {selected && (
          <button type="button" className="link" onClick={() => vizStore.select(null)}>
            clear selection
          </button>
        )}
      </div>
      {showVocab && (
        <div className="vocab">
          {vocab.map(([ev, n]) => (
            <label key={ev} className={filter.hidden.has(ev) ? "chip off" : "chip"}>
              <input type="checkbox" checked={!filter.hidden.has(ev)} onChange={() => toggle(ev)} />
              {ev} <span className="dim">{n}</span>
            </label>
          ))}
        </div>
      )}
      <div className="log-body">
        {shown.map((env) => (
          <Row key={env.seq} env={env} world={world} selected={selected} />
        ))}
      </div>
    </div>
  );
}

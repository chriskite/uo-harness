import type { ReactNode } from "react";
import { vizStore } from "../store.ts";
import type { HexSerial } from "../types.ts";

/** Hex serial that selects the entity on click. */
export function SerialLink({ serial, label, selected }: { serial: HexSerial; label?: string | null; selected?: boolean }) {
  return (
    <button
      type="button"
      className={selected ? "serial selected" : "serial"}
      title={`select ${serial}`}
      onClick={(e) => {
        e.stopPropagation();
        vizStore.select(serial);
      }}
    >
      {serial}
      {label ? <span className="serial-label"> {label}</span> : null}
    </button>
  );
}

export function Panel({ title, extra, children, className }: { title: ReactNode; extra?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={className ? `panel ${className}` : "panel"}>
      <header className="panel-head">
        <h2>{title}</h2>
        {extra}
      </header>
      <div className="panel-body">{children}</div>
    </section>
  );
}

export function Bar({ label, value, max, color }: { label: string; value?: number; max?: number; color: string }) {
  const pct = value !== undefined && max ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="bar">
      <span className="bar-label">{label}</span>
      <div className="bar-track">
        <div className="bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="bar-value">
        {value ?? "?"}/{max ?? "?"}
      </span>
    </div>
  );
}

export function Badge({ kind, children, title }: { kind: "ok" | "warn" | "bad" | "info" | "dim"; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge-${kind}`} title={title}>
      {children}
    </span>
  );
}

/** Shimmering placeholder bars (one per width) while slow data loads; `label` is what screen readers hear. */
export function Skeleton({ widths, label }: { widths: readonly string[]; label: string }) {
  return (
    <div className="skeleton" role="status" aria-busy="true" aria-label={label}>
      {widths.map((w, i) => (
        <div key={i} className="skeleton-bar" style={{ width: w }} />
      ))}
    </div>
  );
}

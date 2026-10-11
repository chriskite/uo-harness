import type { CSSProperties } from "react";
import {
  type Action,
  type Binding,
  byCombo,
  capLabel,
  categoryClass,
  comboId,
  formatCombo,
  type KeyCap,
  LAYERS,
  offBoard,
  TKL_ROWS,
} from "../razor.ts";

export type RazorSel = { kind: "combo"; mod: number; key: number } | { kind: "action"; name: string } | null;

type Props = {
  bindings: Binding[];
  actions: Map<string, Action>;
  view: number;
  sel: RazorSel;
  editable: boolean;
  onSelectCombo: (mod: number, key: number) => void;
  onToggleMod: (flag: number) => void;
  /** Switch the layer to `mod` and select the combo (off-board chips). */
  onShowCombo: (mod: number, key: number) => void;
};

const width = (w: number): CSSProperties => ({ width: `calc(var(--rz-u) * ${w} + ${(w - 1) * 4}px)` });

export function RazorKeyboard({ bindings, actions, view, sel, editable, onSelectCombo, onToggleMod, onShowCombo }: Props) {
  const combos = byCombo(bindings);
  const labelOf = (b: Binding) => actions.get(b.name)?.label ?? b.label ?? b.name;

  /** A bindable cap (keyboard key or mouse zone). */
  const cap = (key: number, label: string, className: string, style?: CSSProperties, keyId?: string) => {
    const here = combos.get(comboId(view, key)) ?? [];
    const top = here[0];
    const action = top ? actions.get(top.name) : undefined;
    const lines: string[] = [];
    let others = 0;
    for (const m of LAYERS) {
      for (const b of combos.get(comboId(m, key)) ?? []) {
        lines.push(`${formatCombo(m, key)} — ${labelOf(b)}`);
        if (m !== view) others++;
      }
    }
    const selected = sel?.kind === "combo" && sel.mod === view && sel.key === key;
    const cls = [className, "rz-cap", top ? `bound ${categoryClass(action)}` : "", selected ? "sel" : "", editable ? "" : "ro"]
      .filter(Boolean)
      .join(" ");
    return (
      <button key={keyId} type="button" className={cls} style={style} title={lines.length ? lines.join("\n") : formatCombo(view, key)} onClick={() => onSelectCombo(view, key)}>
        <span className="rz-cap-key">{label}</span>
        {top && <span className="rz-cap-act">{capLabel(labelOf(top))}</span>}
        {others > 0 && <span className="rz-cap-more">+{others}</span>}
        {here.length > 1 && (
          <span className="rz-cap-warn" title={`${here.length} actions share this key; Razor runs only the first`}>
            !
          </span>
        )}
      </button>
    );
  };

  const renderCap = (c: KeyCap, i: number) => {
    const w = c.w ?? 1;
    const style: CSSProperties = { ...width(w), marginLeft: c.gap ? `calc(var(--rz-u) * ${c.gap} + ${c.gap * 4}px)` : undefined };
    if (c.mod !== undefined) {
      const flag = c.mod;
      return (
        <button key={i} type="button" className={`rz-cap mod${view & flag ? " on" : ""}`} style={style} onClick={() => onToggleMod(flag)} title={`Show the ${c.label} layer`}>
          <span className="rz-cap-key">{c.label}</span>
        </button>
      );
    }
    if (c.disabled || c.key === null) {
      return (
        <span key={i} className="rz-cap disabled" style={style} title="Razor can't bind this key">
          <span className="rz-cap-key">{c.label}</span>
        </span>
      );
    }
    return cap(c.key, c.label, "", style, String(i));
  };

  const off = offBoard(bindings);
  return (
    <div className="rz-board-wrap">
      <div className="rz-board-row">
        <div className="rz-board">
          {TKL_ROWS.map((row, r) => (
            <div key={r} className={r === 0 ? "rz-row rz-row-f" : "rz-row"}>
              {row.map(renderCap)}
            </div>
          ))}
        </div>
        <div className="rz-mouse">
          <div className="rz-mouse-top">
            <span className="rz-mouse-btn disabled" title="Left/right click can't be bound in Razor">
              L
            </span>
            <div className="rz-wheel">
              {cap(-1, "Wheel Up", "rz-zone")}
              {cap(-3, "Middle", "rz-zone")}
              {cap(-2, "Wheel Down", "rz-zone")}
            </div>
            <span className="rz-mouse-btn disabled" title="Left/right click can't be bound in Razor">
              R
            </span>
          </div>
          <div className="rz-mouse-side">
            {cap(-5, "X Button 2", "rz-zone")}
            {cap(-4, "X Button 1", "rz-zone")}
          </div>
        </div>
      </div>
      {off.length > 0 && (
        <div className="rz-offboard">
          <span className="dim">Not on this keyboard:</span>
          {off.map((b) => (
            <button
              key={b.name}
              type="button"
              className={`rz-chip ${categoryClass(actions.get(b.name))}${sel?.kind === "combo" && sel.mod === b.mod && sel.key === b.key ? " sel" : ""}`}
              onClick={() => {
                onShowCombo(b.mod, b.key);
              }}
            >
              {formatCombo(b.mod, b.key)} — {labelOf(b)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

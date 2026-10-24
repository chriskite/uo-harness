// The Sim page's damage charts: stacked bars per 5 s on the timeline's x axis, by source. Left: damage
// taken by the focus (the selected mobile if it is the player or a pet, else the player); right: damage
// the team (player and pets) dealt to monsters, by attacker. A click seeks.
import { useMemo, useRef } from "react";
import { linScale, niceTicks } from "../chart.ts";
import { OTHER, binLabels, damageBins, type DamageBin } from "../sim/damage.ts";
import type { Trace } from "../sim/trace.ts";
import { PAD_L, PAD_R, useWidth } from "./SimTimeline.tsx";

const BIN_S = 5;
const H = 84;
const TOP = 6;
const BOTTOM = 4;
const PALETTE = ["#ef4444", "#a78bfa", "#38bdf8", "#f59e0b", "#4ade80", "#f472b6"];

function Chart({ title, trace, bins, t, onSeek }: { title: string; trace: Trace; bins: DamageBin[]; t: number; onSeek: (t: number) => void }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const width = useWidth(wrapRef);
  const labels = useMemo(() => binLabels(bins), [bins]);
  const color = (l: string) => (l === OTHER ? "#6b7280" : PALETTE[labels.indexOf(l) % PALETTE.length]!);
  const max = Math.max(0, ...bins.map((b) => Object.values(b.by).reduce((a, n) => a + n, 0)));
  const ticks = niceTicks(max, 3);
  const top = ticks[ticks.length - 1]!;
  const y = linScale(0, top, H - BOTTOM, TOP);
  const x = linScale(trace.t0, trace.t1, PAD_L, Math.max(PAD_L + 1, width - PAD_R));
  const total = bins.reduce((n, b) => n + Object.values(b.by).reduce((a, v) => a + v, 0), 0);
  return (
    <div className="sim-chart">
      <h3>
        {title} <span className="dim">({total} total, per {BIN_S} s)</span>
      </h3>
      <div className="sim-svg-wrap" ref={wrapRef}>
        {width > 0 && (
          <svg
            width={width}
            height={H}
            onClick={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              const px = e.clientX - rect.left;
              const f = (px - PAD_L) / Math.max(1, width - PAD_L - PAD_R);
              onSeek(trace.t0 + Math.min(1, Math.max(0, f)) * (trace.t1 - trace.t0));
            }}
          >
            {ticks.map((v) => (
              <g key={v}>
                <line x1={PAD_L} x2={width - PAD_R} y1={y(v)} y2={y(v)} stroke="rgba(255,255,255,0.07)" />
                <text x={PAD_L - 6} y={y(v)} textAnchor="end" dominantBaseline="middle">
                  {v}
                </text>
              </g>
            ))}
            {bins.map((b) => {
              const bx = x(b.t);
              const bw = Math.max(1, x(Math.min(trace.t1, b.t + BIN_S)) - bx - 1);
              let acc = 0;
              return labels.map((l) => {
                const n = b.by[l];
                if (!n) return null;
                const y0 = y(acc);
                acc += n;
                return (
                  <rect key={`${b.t}-${l}`} x={bx} y={y(acc)} width={bw} height={Math.max(0.5, y0 - y(acc))} fill={color(l)}>
                    <title>{`${l}: ${n}`}</title>
                  </rect>
                );
              });
            })}
            <line x1={x(t)} x2={x(t)} y1={TOP} y2={H - BOTTOM} stroke="#f6dc95" strokeWidth={1.5} />
          </svg>
        )}
      </div>
      <div className="sim-legend">
        {labels.map((l) => (
          <span key={l}>
            <span className="sim-swatch" style={{ background: color(l) }} />
            {l}
          </span>
        ))}
        {!labels.length && <span className="dim">no damage</span>}
      </div>
    </div>
  );
}

export function SimDamageCharts({ trace, t, selected, onSeek }: { trace: Trace; t: number; selected: number | null; onSeek: (t: number) => void }) {
  const selKind = selected !== null ? trace.names.get(selected)?.kind : undefined;
  const focus = selected !== null && (selKind === "player" || selKind === "pet") ? selected : (trace.head?.player ?? null);
  const taken = useMemo(() => (focus !== null ? damageBins(trace, { victim: focus }, BIN_S) : []), [trace, focus]);
  const dealt = useMemo(() => damageBins(trace, { team: true }, BIN_S), [trace]);
  return (
    <div className="sim-charts">
      <Chart title={`damage taken by ${trace.nameOf(focus)}`} trace={trace} bins={taken} t={t} onSeek={onSeek} />
      <Chart title="damage dealt by the team to monsters" trace={trace} bins={dealt} t={t} onSeek={onSeek} />
    </div>
  );
}

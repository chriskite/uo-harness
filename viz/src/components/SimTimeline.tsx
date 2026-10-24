// The Sim page's timeline: playback controls and an SVG strip across the run with one lane of markers per
// enabled row (deaths, big hits on the player, casts at the player, potions, vet, recall, abilities and
// notable runner log lines). Click or drag scrubs; hovering a marker shows its event.
import { useEffect, useRef, useState, type PointerEvent, type RefObject } from "react";
import { linScale, timeTicks } from "../chart.ts";
import { fmtClock } from "../format.ts";
import { MARKER_ROWS, ROW_COLOR, ROW_LABEL, describeEvent, fmtSimT, type Marker, type MarkerRow } from "../sim/markers.ts";
import type { Trace } from "../sim/trace.ts";

/** Left and right padding of the timeline and the damage charts, so their x axes line up. */
export const PAD_L = 110;
export const PAD_R = 12;
export const RATES = [0.25, 0.5, 1, 2, 4, 8, 16, 60] as const;
const LANE = 12;
const AXIS = 16;

/** The element's content width, kept current. */
export function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.getBoundingClientRect().width));
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

interface Props {
  trace: Trace;
  t: number;
  onSeek: (t: number) => void;
  playing: boolean;
  onPlay: () => void;
  rate: number;
  onRate: (r: number) => void;
  markers: Marker[];
  rows: ReadonlySet<MarkerRow>;
  onRows: (rows: Set<MarkerRow>) => void;
  only: boolean;
  onOnly: (on: boolean) => void;
  onPrev: () => void;
  onNext: () => void;
}

export function SimTimeline({ trace, t, onSeek, playing, onPlay, rate, onRate, markers, rows, onRows, only, onOnly, onPrev, onNext }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const width = useWidth(wrapRef);
  const [hover, setHover] = useState<{ x: number; y: number; m: Marker } | null>(null);
  const lanes = MARKER_ROWS.filter((r) => rows.has(r));
  const height = lanes.length * LANE + AXIS + 4;
  const x = linScale(trace.t0, trace.t1, PAD_L, Math.max(PAD_L + 1, width - PAD_R));
  const tOf = (px: number) => Math.min(trace.t1, Math.max(trace.t0, trace.t0 + ((px - PAD_L) / Math.max(1, width - PAD_L - PAD_R)) * (trace.t1 - trace.t0)));
  const ticks = timeTicks(trace.t0, trace.t1, Math.max(2, Math.floor(width / 140)));
  const local = (e: PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    return { px: e.clientX - rect.left, py: e.clientY - rect.top };
  };

  const toggle = (r: MarkerRow) => {
    const next = new Set(rows);
    if (next.has(r)) next.delete(r);
    else next.add(r);
    onRows(next);
  };

  return (
    <div className="sim-timeline">
      <div className="sim-controls">
        <button type="button" onClick={onPlay} title="play / pause (Space)">
          {playing ? "❚❚ pause" : "▶ play"}
        </button>
        <select value={rate} onChange={(e) => onRate(Number(e.target.value))} title="playback rate">
          {RATES.map((r) => (
            <option key={r} value={r}>
              {r}×
            </option>
          ))}
        </select>
        <button type="button" onClick={() => onSeek(t - 0.1)} title="back 0.1 s (←; Shift 1 s, Ctrl 10 s)">
          −0.1s
        </button>
        <button type="button" onClick={() => onSeek(t + 0.1)} title="forward 0.1 s (→; Shift 1 s, Ctrl 10 s)">
          +0.1s
        </button>
        <button type="button" onClick={onPrev} title="previous marker ([)">
          ⏮ marker
        </button>
        <button type="button" onClick={onNext} title="next marker (])">
          marker ⏭
        </button>
        <span className="sim-time">{fmtSimT(t)}</span>
        <span className="dim mono" title="the game clock: the trace's epoch + t">
          {trace.head ? fmtClock(trace.head.epoch + t) : ""} / {fmtSimT(trace.t1)}
        </span>
        <span className="sim-rows">
          {MARKER_ROWS.map((r) => (
            <label key={r}>
              <input type="checkbox" checked={rows.has(r)} onChange={() => toggle(r)} />
              <span className="sim-swatch" style={{ background: ROW_COLOR[r] }} />
              {ROW_LABEL[r]}
            </label>
          ))}
          <label>
            <input type="checkbox" checked={only} onChange={(e) => onOnly(e.target.checked)} />
            selected mobile only
          </label>
        </span>
      </div>
      <div className="sim-svg-wrap" ref={wrapRef}>
        {width > 0 && (
          <svg
            width={width}
            height={height}
            onPointerDown={(e) => {
              e.currentTarget.setPointerCapture(e.pointerId);
              onSeek(tOf(local(e).px));
            }}
            onPointerMove={(e) => {
              const { px, py } = local(e);
              if (e.buttons & 1) onSeek(tOf(px));
              const lane = lanes[Math.floor((py - 2) / LANE)];
              let best: Marker | null = null;
              let bestD = 5;
              if (lane) {
                for (const m of markers) {
                  if (m.row !== lane) continue;
                  const d = Math.abs(x(m.t) - px);
                  if (d <= bestD) {
                    bestD = d;
                    best = m;
                  }
                }
              }
              setHover(best ? { x: px, y: py, m: best } : null);
            }}
            onPointerLeave={() => setHover(null)}
          >
            {lanes.map((r, i) => (
              <g key={r}>
                <rect x={PAD_L} y={2 + i * LANE} width={Math.max(0, width - PAD_L - PAD_R)} height={LANE - 2} fill="rgba(255,255,255,0.03)" />
                <text x={PAD_L - 6} y={2 + i * LANE + LANE / 2} textAnchor="end" dominantBaseline="middle">
                  {ROW_LABEL[r]}
                </text>
              </g>
            ))}
            {markers.map((m, i) => {
              const lane = lanes.indexOf(m.row);
              if (lane < 0) return null;
              const mx = x(m.t);
              return <line key={i} x1={mx} x2={mx} y1={3 + lane * LANE} y2={lane * LANE + LANE} stroke={m.color} strokeWidth={1.5} />;
            })}
            {ticks.map((tk) => (
              <text key={tk} x={x(tk)} y={height - 3} textAnchor="middle">
                {fmtSimT(tk)}
              </text>
            ))}
            <line x1={x(t)} x2={x(t)} y1={0} y2={height - AXIS} stroke="#f6dc95" strokeWidth={1.5} />
          </svg>
        )}
        {hover && (
          <div className="sim-tip" style={{ left: Math.min(hover.x + 12, Math.max(0, width - 300)), top: hover.y + 12 }}>
            {fmtSimT(hover.m.t)} {describeEvent(hover.m.ev, trace)}
          </div>
        )}
      </div>
    </div>
  );
}

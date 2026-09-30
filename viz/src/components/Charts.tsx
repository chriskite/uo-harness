// Plain-SVG charts for the Jobs page (geometry in chart.ts).
import { barSlots, linePath, linScale, niceTicks, timeTicks } from "../chart.ts";
import { eventView, fmtNum, fmtStamp, oneLocalDay, type JobEvent, type JobTrip, type RollingPoint } from "../jobs.ts";
import { fmtDuration } from "../format.ts";

const W = 560;
const H = 190;
const M = { l: 40, r: 12, t: 12, b: 24 };

function YAxis({ ticks, y }: { ticks: number[]; y: (v: number) => number }) {
  return (
    <g className="axis">
      {ticks.map((v) => (
        <g key={v}>
          <line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} className="grid" />
          <text x={M.l - 6} y={y(v) + 4} textAnchor="end">
            {fmtNum(v, 1)}
          </text>
        </g>
      ))}
    </g>
  );
}

const STRIP_H = 64;

function Empty({ text, h = H }: { text: string; h?: number }) {
  const inner = h === H ? { y: M.t, h: H - M.t - M.b } : { y: 6, h: h - 12 };
  return (
    <svg className={h === H ? "chart" : "chart strip"} viewBox={`0 0 ${W} ${h}`} role="img">
      <rect x={M.l} y={inner.y} width={W - M.l - M.r} height={inner.h} className="chart-empty" />
      <text x={W / 2} y={h / 2 + 4} textAnchor="middle" className="chart-empty-text">
        {text}
      </text>
    </svg>
  );
}

/** Time labels; the outermost ones anchor inward so they aren't clipped at the edges. */
function TimeAxis({ t0, t1, x, y, sameDay }: { t0: number; t1: number; x: (t: number) => number; y: number; sameDay: boolean }) {
  return (
    <g className="axis">
      {timeTicks(t0, t1, 4).map((t) => {
        const px = x(t);
        const anchor = px < M.l + 24 ? "start" : px > W - M.r - 24 ? "end" : "middle";
        return (
          <text key={t} x={px} y={y} textAnchor={anchor}>
            {fmtStamp(t, sameDay)}
          </text>
        );
      })}
    </g>
  );
}

/** Rolling (window) and cumulative logs/hr at each trip end. */
export function LogsPerHourChart({ points, windowS }: { points: RollingPoint[]; windowS: number }) {
  const pts = points.filter((p) => p.logs_per_hour !== null);
  if (pts.length === 0) return <Empty text="no timed trips yet" />;
  const ymax = Math.max(...pts.flatMap((p) => [p.logs_per_hour ?? 0, p.cum_logs_per_hour ?? 0]));
  const ticks = niceTicks(ymax);
  const t0 = pts[0]!.t;
  const t1 = pts[pts.length - 1]!.t;
  const pad = t1 > t0 ? (t1 - t0) * 0.04 : 0;
  const x = linScale(t0 - pad, t1 + pad, M.l, W - M.r);
  const y = linScale(0, ticks[ticks.length - 1]!, H - M.b, M.t);
  const sameDay = oneLocalDay([t0, t1]);
  const roll = pts.map((p) => [x(p.t), y(p.logs_per_hour ?? 0)] as const);
  const cum = pts.filter((p) => p.cum_logs_per_hour !== null).map((p) => [x(p.t), y(p.cum_logs_per_hour!)] as const);
  return (
    <>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="logs per hour over time">
        <YAxis ticks={ticks} y={y} />
        <TimeAxis t0={t0} t1={t1} x={x} y={H - 6} sameDay={sameDay} />
        <path d={linePath(cum)} className="line line-cum" />
        <path d={linePath(roll)} className="line line-roll" />
        {pts.map((p) => (
          <circle key={p.n} cx={x(p.t)} cy={y(p.logs_per_hour ?? 0)} r={3.5} className="pt">
            <title>
              {`trip #${p.n} ended ${fmtStamp(p.t)}: ${fmtNum(p.logs_per_hour, 0)} logs/hr over the last ${Math.round(windowS / 60)} min (${p.window_trips} trip${p.window_trips === 1 ? "" : "s"}), ${fmtNum(p.cum_logs_per_hour, 0)} cumulative`}
            </title>
          </circle>
        ))}
      </svg>
      <div className="legend">
        <span>
          <i className="sw sw-roll" /> rolling {Math.round(windowS / 60)} min
        </span>
        <span>
          <i className="sw sw-cum" /> cumulative
        </span>
        <span className="dim">active time only; one point per trip end</span>
      </div>
    </>
  );
}

/** Logs per trip as bars, with the mean, captcha dots and death marks. */
export function LogsPerTripChart({ trips }: { trips: JobTrip[] }) {
  if (trips.length === 0) return <Empty text="no trips yet" />;
  const ticks = niceTicks(Math.max(...trips.map((t) => Math.max(t.logs, t.stored))));
  const y = linScale(0, ticks[ticks.length - 1]!, H - M.b, M.t);
  const slots = barSlots(trips.length, M.l, W - M.r);
  const mean = trips.reduce((a, t) => a + t.logs, 0) / trips.length;
  const every = Math.ceil(trips.length / 20);
  return (
    <>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="logs per trip">
        <YAxis ticks={ticks} y={y} />
        {trips.map((t, i) => {
          const s = slots[i]!;
          const deaths = t.events.death ?? 0;
          return (
            <g key={t.n}>
              <rect x={s.x} y={y(t.logs)} width={s.w} height={Math.max(0, y(0) - y(t.logs))} className="bar-logs">
                <title>
                  {`trip #${t.n}${t.t_start ? ` · ${fmtStamp(t.t_start)}` : ""}: ${t.logs} logs, ${t.stored} stored` +
                    `${t.duration_s ? ` in ${fmtDuration(t.duration_s)}` : ""}` +
                    `${t.captchas ? ` · ${t.captchas} captcha` : ""}${deaths ? ` · ${deaths} death` : ""}`}
                </title>
              </rect>
              <line x1={s.x} x2={s.x + s.w} y1={y(t.stored)} y2={y(t.stored)} className="mark-stored" />
              {t.captchas > 0 && <circle cx={s.cx} cy={y(t.logs) - 8} r={3.5} className="mark-captcha" />}
              {deaths > 0 && (
                <text x={s.cx} y={y(t.logs) - (t.captchas ? 16 : 6)} textAnchor="middle" className="mark-death">
                  ✕
                </text>
              )}
              {i % every === 0 && (
                <text x={s.cx} y={H - 6} textAnchor="middle" className="axis-label">
                  #{t.n}
                </text>
              )}
            </g>
          );
        })}
        <line x1={M.l} x2={W - M.r} y1={y(mean)} y2={y(mean)} className="mean" />
      </svg>
      <div className="legend">
        <span>
          <i className="sw sw-bar" /> logs
        </span>
        <span>
          <i className="sw sw-stored" /> boards stored
        </span>
        <span>
          <i className="sw sw-captcha" /> captcha
        </span>
        <span>
          <i className="sw sw-mean" /> mean {fmtNum(mean, 1)}
        </span>
        <span>
          <span className="mark-death-key">✕</span> death
        </span>
      </div>
    </>
  );
}

const EVENT_CLASS: Record<string, string> = { death: "ev-death", theft: "ev-theft", pk_seen: "ev-pk", flee: "ev-flee" };

/** Trips as grey spans and job events as coloured marks on one time axis. */
export function EventStrip({ trips, events }: { trips: JobTrip[]; events: JobEvent[] }) {
  const stamps = [...trips.flatMap((t) => [t.t_start, t.t_end]), ...events.map((e) => e.t)].filter((t): t is number => t !== null);
  if (stamps.length === 0) return <Empty text="nothing recorded yet" h={STRIP_H} />;
  const t0 = Math.min(...stamps);
  const t1 = Math.max(...stamps);
  const sh = STRIP_H;
  const x = linScale(t0, t1, M.l, W - M.r);
  const sameDay = oneLocalDay([t0, t1]);
  return (
    <svg className="chart strip" viewBox={`0 0 ${W} ${sh}`} role="img" aria-label="trips and job events over time">
      <line x1={M.l} x2={W - M.r} y1={24} y2={24} className="grid" />
      {trips.map((t) =>
        t.t_start !== null && t.t_end !== null ? (
          <rect key={t.n} x={x(t.t_start)} y={17} width={Math.max(2, x(t.t_end) - x(t.t_start))} height={14} className="span-trip">
            <title>{`trip #${t.n}: ${fmtStamp(t.t_start)}–${fmtStamp(t.t_end, true)}, ${t.logs} logs`}</title>
          </rect>
        ) : null,
      )}
      {events.map((e, i) => {
        const v = eventView(e);
        return (
          <circle key={e.id ?? i} cx={x(e.t)} cy={24} r={5} className={`ev-mark ${EVENT_CLASS[e.kind] ?? "ev-other"}`}>
            <title>{`${fmtStamp(e.t)} ${v.label}${v.detail ? ` · ${v.detail}` : ""}`}</title>
          </circle>
        );
      })}
      <TimeAxis t0={t0} t1={t1} x={x} y={sh - 6} sameDay={sameDay} />
    </svg>
  );
}

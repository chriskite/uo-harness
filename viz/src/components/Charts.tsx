// Plain-SVG charts for the Jobs page (geometry in chart.ts).
import { barSlots, linePath, linScale, niceTicks, timeTicks } from "../chart.ts";
import { eventView, fmtInt, fmtNum, fmtStamp, oneLocalDay, type HuntVisit, type JobEvent, type JobTrip } from "../jobs.ts";
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

export interface RateSeries {
  label: string;
  /** `line-<cls>` / `sw-<cls>` styles */
  cls: string;
  /** one value per point; null = no rate there */
  values: (number | null)[];
  /** dots with a tooltip on this series' points */
  dots?: boolean;
}

/** Rates at each trip / visit end, one line per series (all share the points' times). */
export function RateChart({ points, series, what, unit, note }: { points: { t: number; n: number }[]; series: RateSeries[]; what: string; unit: string; note: string }) {
  const idx = points.map((_, i) => i).filter((i) => series.some((s) => s.values[i] !== null && s.values[i] !== undefined));
  if (idx.length === 0) return <Empty text={`no timed ${what}s yet`} />;
  const val = (s: RateSeries, i: number) => s.values[i] ?? null;
  const ymax = Math.max(...idx.flatMap((i) => series.map((s) => val(s, i) ?? 0)));
  const ticks = niceTicks(ymax);
  const t0 = points[idx[0]!]!.t;
  const t1 = points[idx[idx.length - 1]!]!.t;
  const pad = t1 > t0 ? (t1 - t0) * 0.04 : 0;
  const x = linScale(t0 - pad, t1 + pad, M.l, W - M.r);
  const y = linScale(0, ticks[ticks.length - 1]!, H - M.b, M.t);
  const sameDay = oneLocalDay([t0, t1]);
  const tip = (i: number) =>
    `${what} #${points[i]!.n} ended ${fmtStamp(points[i]!.t)}: ` + series.map((s) => `${s.label} ${fmtNum(val(s, i), 0)} ${unit}`).join(", ");
  return (
    <>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${unit} over time`}>
        <YAxis ticks={ticks} y={y} />
        <TimeAxis t0={t0} t1={t1} x={x} y={H - 6} sameDay={sameDay} />
        {[...series].reverse().map((s) => (
          <path key={s.cls} d={linePath(idx.filter((i) => val(s, i) !== null).map((i) => [x(points[i]!.t), y(val(s, i)!)] as const))} className={`line line-${s.cls}`} />
        ))}
        {series
          .filter((s) => s.dots)
          .flatMap((s) =>
            idx
              .filter((i) => val(s, i) !== null)
              .map((i) => (
                <circle key={`${s.cls}-${points[i]!.n}`} cx={x(points[i]!.t)} cy={y(val(s, i)!)} r={3.5} className={`pt pt-${s.cls}`}>
                  <title>{tip(i)}</title>
                </circle>
              )),
          )}
      </svg>
      <div className="legend">
        {series.map((s) => (
          <span key={s.cls}>
            <i className={`sw sw-${s.cls}`} /> {s.label}
          </span>
        ))}
        <span className="dim">{note}</span>
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

const SPLIT_KEYS = ["travel", "lockout", "field", "other"] as const;
const SPLIT_LABEL: Record<(typeof SPLIT_KEYS)[number], string> = {
  travel: "recalls + walk to the library",
  lockout: "travel lockout",
  field: "field (chopping, between trees)",
  other: "walks, convert, bank",
};

/** Where each trip's time went (harness/jobs.py time_split) as stacked bars in minutes. */
export function TimeSplitChart({ trips }: { trips: JobTrip[] }) {
  const split = trips.filter((t) => t.time_split !== null);
  if (split.length === 0) return <Empty text="no timed trips yet" />;
  const ticks = niceTicks(Math.max(...split.map((t) => SPLIT_KEYS.reduce((a, k) => a + t.time_split![k], 0) / 60)));
  const y = linScale(0, ticks[ticks.length - 1]!, H - M.b, M.t);
  const slots = barSlots(split.length, M.l, W - M.r);
  const every = Math.ceil(split.length / 20);
  return (
    <>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="trip time split">
        <YAxis ticks={ticks} y={y} />
        {split.map((t, i) => {
          const s = slots[i]!;
          let acc = 0;
          const tip =
            `trip #${t.n} ${t.spot ?? ""} (${t.outcome})${t.t_start ? ` · ${fmtStamp(t.t_start)}` : ""}: ` +
            SPLIT_KEYS.map((k) => `${k} ${fmtDuration(t.time_split![k])}`).join(", ");
          return (
            <g key={t.n}>
              {SPLIT_KEYS.map((k) => {
                const v = t.time_split![k] / 60;
                const top = y(acc + v);
                const h = Math.max(0, y(acc) - top);
                acc += v;
                return (
                  <rect key={k} x={s.x} y={top} width={s.w} height={h} className={`seg seg-${k}`}>
                    <title>{tip}</title>
                  </rect>
                );
              })}
              {t.outcome !== "banked" && (
                <text x={s.cx} y={y(acc) - 4} textAnchor="middle" className="mark-abort">
                  {t.place_fail ? "∅" : "!"}
                  <title>{t.why ?? t.outcome}</title>
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
      </svg>
      <div className="legend">
        {SPLIT_KEYS.map((k) => (
          <span key={k}>
            <i className={`sw sw-seg seg-${k}`} /> {SPLIT_LABEL[k]}
          </span>
        ))}
        <span className="dim">minutes · ! aborted · ∅ the place gave nothing</span>
      </div>
    </>
  );
}

/** XP per visit as bars, with the gold mark, the kill count on top, death marks and the mean. */
export function VisitBarsChart({ visits }: { visits: HuntVisit[] }) {
  if (visits.length === 0) return <Empty text="no visits yet" />;
  const ticks = niceTicks(Math.max(...visits.map((v) => Math.max(v.xp, v.gold))));
  const y = linScale(0, ticks[ticks.length - 1]!, H - M.b, M.t);
  const slots = barSlots(visits.length, M.l, W - M.r);
  const mean = visits.reduce((a, v) => a + v.xp, 0) / visits.length;
  const every = Math.ceil(visits.length / 20);
  return (
    <>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="XP and gold per visit">
        <YAxis ticks={ticks} y={y} />
        {visits.map((v, i) => {
          const s = slots[i]!;
          const deaths = v.events.death ?? 0;
          const top = y(Math.max(v.xp, v.gold));
          return (
            <g key={v.n}>
              <rect x={s.x} y={y(v.xp)} width={s.w} height={Math.max(0, y(0) - y(v.xp))} className="bar-xp">
                <title>
                  {`visit #${v.n}${v.t_start ? ` · ${fmtStamp(v.t_start)}` : ""}: ${v.kills} kill${v.kills === 1 ? "" : "s"}, ${fmtInt(v.xp)} XP, ${fmtInt(v.gold)} gold` +
                    `${v.duration_s ? ` in ${fmtDuration(v.duration_s)}` : ""}${v.ended ? ` · ended: ${v.ended}` : ""}${deaths ? ` · ${deaths} death` : ""}`}
                </title>
              </rect>
              <line x1={s.x} x2={s.x + s.w} y1={y(v.gold)} y2={y(v.gold)} className="mark-gold" />
              {s.w >= 10 && (
                <text x={s.cx} y={top - 4} textAnchor="middle" className="axis-label">
                  {v.kills}
                </text>
              )}
              {deaths > 0 && (
                <text x={s.cx} y={top - (s.w >= 10 ? 16 : 6)} textAnchor="middle" className="mark-death">
                  ✕
                </text>
              )}
              {i % every === 0 && (
                <text x={s.cx} y={H - 6} textAnchor="middle" className="axis-label">
                  #{v.n}
                </text>
              )}
            </g>
          );
        })}
        <line x1={M.l} x2={W - M.r} y1={y(mean)} y2={y(mean)} className="mean" />
      </svg>
      <div className="legend">
        <span>
          <i className="sw sw-xp-bar" /> XP
        </span>
        <span>
          <i className="sw sw-gold" /> gold looted
        </span>
        <span className="dim">number above: kills</span>
        <span>
          <i className="sw sw-mean" /> mean {fmtNum(mean, 0)} XP
        </span>
        <span>
          <span className="mark-death-key">✕</span> death
        </span>
      </div>
    </>
  );
}

const EVENT_CLASS: Record<string, string> = {
  death: "ev-death",
  theft: "ev-theft",
  pk_seen: "ev-pk",
  flee: "ev-flee",
  leave: "ev-flee",
  speech_hold: "ev-theft",
  speech_clear: "ev-ok",
};

/** A trip or visit on the strip: grey span from t_start to t_end. */
export interface Span {
  n: number;
  t_start: number | null;
  t_end: number | null;
  label: string;
}

/** Trips / visits as grey spans and job events as coloured marks on one time axis. */
export function EventStrip({ spans, events }: { spans: Span[]; events: JobEvent[] }) {
  const stamps = [...spans.flatMap((t) => [t.t_start, t.t_end]), ...events.map((e) => e.t)].filter((t): t is number => t !== null);
  if (stamps.length === 0) return <Empty text="nothing recorded yet" h={STRIP_H} />;
  const t0 = Math.min(...stamps);
  const t1 = Math.max(...stamps);
  const sh = STRIP_H;
  const x = linScale(t0, t1, M.l, W - M.r);
  const sameDay = oneLocalDay([t0, t1]);
  return (
    <svg className="chart strip" viewBox={`0 0 ${W} ${sh}`} role="img" aria-label="trips and job events over time">
      <line x1={M.l} x2={W - M.r} y1={24} y2={24} className="grid" />
      {spans.map((t) =>
        t.t_start !== null && t.t_end !== null ? (
          <rect key={t.n} x={x(t.t_start)} y={17} width={Math.max(2, x(t.t_end) - x(t.t_start))} height={14} className="span-trip">
            <title>{`${t.label}: ${fmtStamp(t.t_start)}–${fmtStamp(t.t_end, true)}`}</title>
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

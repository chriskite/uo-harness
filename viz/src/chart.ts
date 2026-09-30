// Plain-SVG chart geometry for the Jobs page (no chart library): linear scales,
// "nice" axis ticks, polyline paths and bar slots.

/** Linear map from [d0, d1] onto [r0, r1]; a zero-width domain maps to the range middle. */
export function linScale(d0: number, d1: number, r0: number, r1: number): (v: number) => number {
  if (d1 === d0) return () => (r0 + r1) / 2;
  const k = (r1 - r0) / (d1 - d0);
  return (v) => r0 + (v - d0) * k;
}

/** Round step (1, 2, 2.5, 5 × 10^n) giving about `count` intervals over [0, max]. */
export function niceStep(max: number, count = 4): number {
  if (!(max > 0)) return 1;
  const raw = max / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  return step;
}

/** Ticks 0, step, … up to the first tick >= max (so the axis top is a round number).
 * max <= 0 (empty data) gives [0, 1]. */
export function niceTicks(max: number, count = 4): number[] {
  if (!(max > 0)) return [0, 1];
  const step = niceStep(max, count);
  const out: number[] = [];
  for (let v = 0; ; v += step) {
    out.push(Number(v.toFixed(10)));
    if (v >= max - 1e-9) break;
  }
  return out;
}

/** "M x y L x y …" through the points, rounded to 0.1 px; "" for none. */
export function linePath(points: readonly (readonly [number, number])[]): string {
  return points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${Math.round(x * 10) / 10} ${Math.round(y * 10) / 10}`).join(" ");
}

/** n equal bar slots across [x0, x1]: each bar is `fill` of its slot, centered,
 * at most `maxW` wide (a single trip doesn't become a wall). */
export function barSlots(n: number, x0: number, x1: number, fill = 0.7, maxW = 48): { x: number; w: number; cx: number }[] {
  if (n <= 0) return [];
  const slot = (x1 - x0) / n;
  const w = Math.min(slot * fill, maxW);
  return Array.from({ length: n }, (_, i) => {
    const cx = x0 + slot * (i + 0.5);
    return { x: cx - w / 2, w, cx };
  });
}

/** `count` evenly spaced time ticks across [t0, t1] (both ends included); one tick when t0 == t1. */
export function timeTicks(t0: number, t1: number, count = 4): number[] {
  if (!(t1 > t0)) return [t0];
  return Array.from({ length: count + 1 }, (_, i) => t0 + ((t1 - t0) * i) / count);
}

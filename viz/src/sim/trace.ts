// Parsing a sim trace (types.ts) and rebuilding the state at any time: the last keyframe at or before t,
// then the deltas after it up to t. Plus the short-lived effects the map draws at t.
import type { DeltaLine, EndLine, Frame, ItemRec, KeyLine, MobRec, TraceEvent, TraceHead, WorldRec } from "./types.ts";

export interface MobName {
  name: string;
  key: string | null;
  kind: string;
}

const EMPTY_WORLD: WorldRec = { songs: {}, pen: 0, par: 0, ham: 0, pot: 0, rc: null, vet: 0, pvp: 0 };

/** Index of the first element with t > `t` (elements sorted by t). */
function upper<T extends { t: number }>(xs: readonly T[], t: number): number {
  let lo = 0;
  let hi = xs.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (xs[mid]!.t <= t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** Index of the first element with t >= `t`. */
function lower<T extends { t: number }>(xs: readonly T[], t: number): number {
  let lo = 0;
  let hi = xs.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (xs[mid]!.t < t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

export class Trace {
  /** Each keyframe with `di`: how many deltas came before it in the file (those it already contains). */
  readonly keys: (KeyLine & { di: number })[] = [];
  readonly deltas: DeltaLine[] = [];
  readonly events: TraceEvent[] = [];
  readonly names = new Map<number, MobName>();
  head: TraceHead | null = null;
  end: EndLine | null = null;
  t0 = 0;
  t1 = 0;

  /** The state at `t` (before the first keyframe: the first keyframe). Records are copies; read-only use. */
  stateAt(t: number): Frame {
    const ki = Math.max(0, upper(this.keys, t) - 1);
    const key = this.keys[ki];
    const mobs = new Map<number, MobRec>();
    const items = new Map<number, ItemRec>();
    if (!key) return { t, mobs, w: { ...EMPTY_WORLD }, items };
    // shallow copies: deltas replace whole fields, never mutate nested values
    for (const m of key.mobs) mobs.set(m.s, { ...m });
    for (const it of key.items) items.set(it.s, it);
    const w: WorldRec = { ...EMPTY_WORLD, ...key.w };
    for (let i = key.di; i < this.deltas.length; i++) {
      const d = this.deltas[i]!;
      if (d.t > t) break;
      if (d.m) {
        for (const [s, ch] of Object.entries(d.m)) {
          const m = mobs.get(Number(s));
          if (m) Object.assign(m, ch);
        }
      }
      for (const m of d.add ?? []) mobs.set(m.s, { ...m });
      for (const s of d.rm ?? []) mobs.delete(s);
      if (d.w) Object.assign(w, d.w);
      for (const it of d.ia ?? []) items.set(it.s, it);
      for (const s of d.ir ?? []) items.delete(s);
    }
    return { t, mobs, w, items };
  }

  /** Events with a <= t <= b, in time order. */
  eventsIn(a: number, b: number, pred?: (e: TraceEvent) => boolean): TraceEvent[] {
    const out: TraceEvent[] = [];
    for (let i = lower(this.events, a); i < this.events.length; i++) {
      const e = this.events[i]!;
      if (e.t > b) break;
      if (!pred || pred(e)) out.push(e);
    }
    return out;
  }

  /** The last `n` events at or before t matching `pred`, in time order. */
  lastEvents(t: number, n: number, pred: (e: TraceEvent) => boolean): TraceEvent[] {
    const out: TraceEvent[] = [];
    for (let i = upper(this.events, t) - 1; i >= 0 && out.length < n; i--) {
      const e = this.events[i]!;
      if (pred(e)) out.push(e);
    }
    return out.reverse();
  }

  /** A mobile's display name (its name, else its key, else the serial). */
  nameOf(s: number | null | undefined): string {
    if (s === null || s === undefined) return "—";
    const n = this.names.get(s);
    return n?.name || n?.key || String(s);
  }
}

function remember(tr: Trace, m: Partial<MobRec> & { s: number }) {
  const was = tr.names.get(m.s);
  tr.names.set(m.s, {
    name: m.name ?? was?.name ?? "",
    key: m.key !== undefined ? m.key : (was?.key ?? null),
    kind: m.kind ?? was?.kind ?? "",
  });
}

export function parseTrace(text: string): Trace {
  const tr = new Trace();
  let tMin = Infinity;
  let tMax = -Infinity;
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    const o = JSON.parse(line) as { k: string; t?: number };
    if (typeof o.t === "number") {
      tMin = Math.min(tMin, o.t);
      tMax = Math.max(tMax, o.t);
    }
    switch (o.k) {
      case "head":
        tr.head = o as TraceHead;
        break;
      case "key": {
        const k = o as KeyLine;
        tr.keys.push({ ...k, di: tr.deltas.length });
        for (const m of k.mobs) remember(tr, m);
        break;
      }
      case "d": {
        const d = o as DeltaLine;
        tr.deltas.push(d);
        for (const m of d.add ?? []) remember(tr, m);
        for (const [s, ch] of Object.entries(d.m ?? {})) {
          if (ch.name !== undefined || ch.key !== undefined || ch.kind !== undefined) remember(tr, { ...ch, s: Number(s) });
        }
        break;
      }
      case "e":
        tr.events.push(o as TraceEvent);
        break;
      case "end":
        tr.end = o as EndLine;
        break;
    }
  }
  // the file is written in sim-time order; the stable sorts only guard against a stray line
  tr.keys.sort((a, b) => a.t - b.t);
  tr.events.sort((a, b) => a.t - b.t);
  tr.t0 = tr.keys[0]?.t ?? (Number.isFinite(tMin) ? tMin : 0);
  tr.t1 = tr.end?.t ?? (Number.isFinite(tMax) ? tMax : tr.t0);
  return tr;
}

export interface Projectile {
  by: number | null;
  v: number | null;
  /** Release tile and the victim's tile now; `f` is the flight fraction done. */
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  f: number;
  x: number;
  y: number;
  spell: string | null;
}

export interface CastFx {
  by: number;
  v: number;
  start: number;
  end: number;
  /** 0..1 of the cast done. */
  f: number;
}

export interface SwingFx {
  by: number;
  v: number;
  hit: boolean;
  age: number;
}

export interface Floater {
  m: number;
  n: number;
  /** The hurt's kind (melee, spell, poison, …), or "heal". */
  kind: string;
  age: number;
}

export interface SayFx {
  m: number;
  text: string;
  age: number;
}

export interface Effects {
  projectiles: Projectile[];
  casts: CastFx[];
  swings: SwingFx[];
  floaters: Floater[];
  says: SayFx[];
}

export const SWING_S = 0.4;
export const FLOAT_S = 1.5;
export const SAY_S = 2.5;
/** Longest flight looked back for (measured flights are well under this). */
const MAX_FLIGHT_S = 10;

export function effectsAt(trace: Trace, frame: Frame, t: number): Effects {
  const out: Effects = { projectiles: [], casts: [], swings: [], floaters: [], says: [] };
  for (const e of trace.eventsIn(t - MAX_FLIGHT_S, t, (e) => e.e === "release")) {
    const land = e.land ?? e.t;
    if (!(t >= e.t && t < land) || e.x === undefined || e.y === undefined) continue;
    const vic = e.v !== null && e.v !== undefined ? frame.mobs.get(e.v) : undefined;
    const x1 = vic?.x ?? e.x;
    const y1 = vic?.y ?? e.y;
    const f = (t - e.t) / (land - e.t);
    out.projectiles.push({
      by: e.by ?? null,
      v: e.v ?? null,
      x0: e.x,
      y0: e.y,
      x1,
      y1,
      f,
      x: e.x + (x1 - e.x) * f,
      y: e.y + (y1 - e.y) * f,
      spell: e.spell ?? null,
    });
  }
  for (const m of frame.mobs.values()) {
    if (!m.cs) continue;
    const [end, v, start] = m.cs;
    out.casts.push({ by: m.s, v, start, end, f: end > start ? Math.min(1, Math.max(0, (t - start) / (end - start))) : 1 });
  }
  for (const e of trace.eventsIn(t - SAY_S, t)) {
    const age = t - e.t;
    if (e.e === "swing" && age <= SWING_S && e.by != null && e.v != null) out.swings.push({ by: e.by, v: e.v, hit: !!e.hit, age });
    else if (e.e === "hurt" && age <= FLOAT_S && e.v != null) out.floaters.push({ m: e.v, n: e.n ?? 0, kind: e.kind ?? "", age });
    else if (e.e === "heal" && age <= FLOAT_S && e.m != null) out.floaters.push({ m: e.m, n: e.n ?? 0, kind: "heal", age });
    else if (e.e === "say" && e.m != null && e.text) out.says.push({ m: e.m, text: e.text, age });
  }
  return out;
}

// The left-column Hunt job pane's model: whether a hunt run is going, what it is doing, and its
// rates right now: gold looted and kills per hour from the runner's run tally (agent_intent
// events), damage per second from the "-N" overhead numbers (speech_heard events).
import { toHex } from "./serial.ts";
import type { AgentIntent, EventEnvelope, HexSerial, Snapshot } from "./types.ts";

/** Intent kinds that end a hunt run (loop_hunt.py: run's done, stop_intent's stopped). */
export const HUNT_ENDED: Record<string, true> = { done: true, stopped: true };

/** The current intent when it belongs to a running hunt run, else null. */
export function runningHunt(intent: AgentIntent | null | undefined): AgentIntent | null {
  return intent?.loop === "hunt" && !HUNT_ENDED[intent.kind ?? ""] ? intent : null;
}

export type HuntActivity = "fighting" | "looting" | "healing" | "hunting" | "waiting" | "travelling" | "banking";

/** What the run is doing, by intent kind (a kind outside the hunt runner's vocabulary has none). */
export const HUNT_ACTIVITY: Record<string, HuntActivity> = {
  attack: "fighting",
  cast: "fighting",
  fight: "fighting",
  approach: "fighting",
  bard: "fighting",
  pets: "fighting",
  standoff: "fighting",
  evade: "fighting",
  retreat: "fighting",
  loot: "looting",
  heal: "healing",
  vet: "healing",
  rest: "healing",
  patrol: "hunting",
  hold: "hunting",
  to_spot: "hunting",
  reposition: "hunting",
  equip: "hunting",
  wait: "waiting",
  enter: "travelling",
  leave: "travelling",
  find_banker: "banking",
  to_bank: "banking",
  open_bank: "banking",
  store: "banking",
};

/** Outlands' overhead damage number ("-25"); it sends no 0x0B (loop_hunt DAMAGE_TEXT). */
const DAMAGE_TEXT = /^-(\d+)$/;

export interface DamageHit {
  t: number;
  serial: HexSerial;
  amount: number;
}

/** The run tally's gold and kills as of an agent_intent event. */
export interface TallySample {
  t: number;
  started: number;
  gold: number;
  kills: number;
}

/** What the stream fold keeps for the pane (events.ts Aggregates.hunt). */
export interface HuntStream {
  damage: DamageHit[];
  tally: TallySample[];
  /** Time of the first event folded: the damage history covers no more than this. */
  from: number | null;
}

/** Damage kept for the DPS window; tally samples kept for the gold window (plus the one before it). */
export const DAMAGE_KEEP_S = 60;
export const TALLY_KEEP_S = 900;

/** Fold one envelope (mutates `h`): a damage number, or a hunt intent whose gold, kills or run changed. */
export function foldHunt(h: HuntStream, env: EventEnvelope): void {
  const d = env.data;
  h.from ??= env.t;
  if (env.origin === "world" && d.ev === "speech_heard") {
    const m = typeof d.text === "string" ? DAMAGE_TEXT.exec(d.text) : null;
    const serial = m ? toHex(d.serial) : null;
    if (!m || serial === null) return;
    h.damage.push({ t: env.t, serial, amount: Number(m[1]) });
    while (h.damage.length && h.damage[0]!.t < env.t - DAMAGE_KEEP_S) h.damage.shift();
    return;
  }
  if (env.origin !== "proxy" || d.ev !== "agent_intent") return;
  const i = d.intent as AgentIntent | null | undefined;
  const gold = i?.tally?.gold;
  const kills = i?.tally?.kills ?? 0;
  if (i?.loop !== "hunt" || typeof gold !== "number" || typeof i.started !== "number") return;
  const last = h.tally[h.tally.length - 1];
  if (last && last.started === i.started && last.gold === gold && last.kills === kills) return;
  h.tally.push({ t: env.t, started: i.started, gold, kills });
  while (h.tally.length > 1 && h.tally[1]!.t <= env.t - TALLY_KEEP_S) h.tally.shift();
}

/** The gold rate's trailing window, and the least span it needs before it shows. */
export const RATE_WINDOW_S = 300;
export const RATE_MIN_S = 60;

export interface TallyRate {
  /** gained per hour over the span */
  perH: number;
  /** seconds the rate is measured over (up to the window) */
  spanS: number;
}

/** `key` (gold or kills) gained per hour right now: since the value at `windowS` ago, over the time to `now`.
 *  The tally is 0 at the run's start, so a window reaching back past it starts there. Otherwise it starts
 *  from the newest sample at or before `windowS` ago: a fold sample (every intent update carries the tally
 *  and the fold keeps each change, so its value still held at the window's start) or, from before the
 *  fold's first sample (the page loaded mid-run), a closed history step's tally at its `until`, which
 *  starts the window there. With neither, the window starts at the earliest sample. Null without a tally
 *  or `started`, or under RATE_MIN_S. */
export function tallyRate(
  samples: readonly TallySample[],
  history: readonly AgentIntent[] | undefined,
  intent: AgentIntent,
  key: "gold" | "kills",
  now: number | null,
  windowS = RATE_WINDOW_S,
): TallyRate | null {
  const cur = intent.tally?.[key];
  if (now === null || typeof cur !== "number" || intent.started === undefined) return null;
  const cutoff = now - windowS;
  let base: [number, number] | null = null;
  if (intent.started >= cutoff) base = [intent.started, 0];
  else {
    const run = samples.filter((s) => s.started === intent.started);
    const foldFrom = run[0]?.t ?? Infinity;
    // [t, value, held]: held = the value still held at any later time up to the next sample (fold samples)
    const pts: [number, number, boolean][] = [];
    for (const h of history ?? []) {
      const v = h.tally?.[key];
      if (h.until !== undefined && h.until < foldFrom && h.started === intent.started && typeof v === "number")
        pts.push([h.until, v, false]);
    }
    for (const s of run) pts.push([s.t, s[key], true]);
    let at: [number, number, boolean] | undefined;
    for (const p of pts) if (p[0] <= cutoff) at = p;
    base = at ? [at[2] ? cutoff : at[0], at[1]] : pts[0] ? [pts[0][0], pts[0][1]] : null;
  }
  if (!base || now - base[0] < RATE_MIN_S) return null;
  const spanS = now - base[0];
  return { perH: ((cur - base[1]) / spanS) * 3600, spanS };
}

/** ClassicUO Mobile.IsHuman bodies (harness/threats.py HUMAN_BODIES). */
const HUMAN_BODIES: ReadonlySet<number> = new Set([
  0x190, 0x191, 0x192, 0x193, 0xb7, 0xb8, 0xb9, 0xba, 0x25d, 0x25e, 0x25f, 0x260, 0x29a, 0x29b, 0x2b6, 0x2b7,
  0x3db, 0x3df, 0x3e2, 0x2e8, 0x2e9, 0x4e5,
]);
const FLAG_PLAYER_HINT = 0x20; // EntityFlags.Movable: players only in captures (threats.py)
const ALLY = 2;

export type DamageSide = "dealt" | "taken";

/** Whose side a damage number is on: taken by us or a green mobile (our pets, guild), dealt to a
 *  hostile creature (notoriety 3-6, not a player), else null (players, innocents such as others'
 *  pets, vendors, a mobile the world doesn't know). Numbers carry no attacker, so "dealt" includes
 *  what anyone else nearby did to those creatures. */
export function damageSide(serial: HexSerial, world: Snapshot | null | undefined): DamageSide | null {
  if (!world) return null;
  if (serial === world.self.serial) return "taken";
  const m = world.mobiles[serial] ?? world.last_seen?.[serial];
  if (!m) return null;
  if (m.notoriety === ALLY) return "taken";
  if (HUMAN_BODIES.has(m.graphic ?? -1) && ((m.flags ?? 0) & FLAG_PLAYER_HINT) !== 0) return null; // a player
  return m.notoriety !== undefined && m.notoriety >= 3 && m.notoriety <= 6 ? "dealt" : null;
}

/** The DPS window, and the least history it needs before it shows. */
export const DPS_WINDOW_S = 15;
export const DPS_MIN_S = 5;

export interface DamageRates {
  /** damage per second dealt to hostile creatures / taken by us and our pets */
  dealt: number;
  taken: number;
  /** seconds the rates are measured over (shorter right after the page loaded) */
  spanS: number;
}

/** Damage per second over the `windowS` before `now`, by damageSide; null under DPS_MIN_S of history. */
export function damageRates(
  stream: HuntStream,
  world: Snapshot | null | undefined,
  now: number | null,
  windowS = DPS_WINDOW_S,
): DamageRates | null {
  if (now === null || stream.from === null) return null;
  const spanS = Math.min(windowS, now - stream.from);
  if (spanS < DPS_MIN_S) return null;
  let dealt = 0;
  let taken = 0;
  for (const h of stream.damage) {
    if (h.t <= now - spanS || h.t > now) continue;
    const side = damageSide(h.serial, world);
    if (side === "dealt") dealt += h.amount;
    else if (side === "taken") taken += h.amount;
  }
  return { dealt: dealt / spanS, taken: taken / spanS, spanS };
}

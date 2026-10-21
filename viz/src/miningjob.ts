// The left-column Mining job pane's model: whether a mining run is going, what it is doing, the
// ore it has dug (the runner's per-leg tally in its to_stand/dig intent texts) and its rate right
// now, this trip's finished legs (/api/jobs?job=mining leg rows) and what the pack holds now.
import { LAYER_BACKPACK } from "./containers.ts";
import type { MiningLeg, OpenLeg } from "./jobs.ts";
import type { AgentIntent, HexSerial, Snapshot } from "./types.ts";

/** Intent kinds that end a mining run (loop_lumber.py run: done, break_due, stop_intent's stopped). trip_done is between trips. */
export const MINING_ENDED: Record<string, true> = { done: true, break_due: true, stopped: true };

/** The current intent when it belongs to a running mining run, else null. */
export function runningMining(intent: AgentIntent | null | undefined): AgentIntent | null {
  return intent?.loop === "mining" && !MINING_ENDED[intent.kind ?? ""] ? intent : null;
}

export type MiningPhase = "out" | "dig" | "smelt" | "lockout" | "escape" | "hop" | "home";

const PHASE: Record<string, MiningPhase> = {
  leave_room: "out",
  resupply: "out",
  mount: "out",
  aspect: "out",
  to_library: "out",
  recall_out: "out",
  to_stand: "dig",
  dig: "dig",
  to_forge: "smelt",
  smelt: "smelt",
  lockout: "lockout",
  escape: "escape",
  flee: "escape",
  hop_plan: "hop",
  hop: "hop",
  recall_home: "home",
  to_room: "home",
  convert: "home",
  store: "home",
  trip_done: "home",
};

/** Phases at the spot: their time is field time (mining_opt.trip_obs: the harvest phase less the walk out and the lockout). */
export const FIELD_PHASES: Partial<Record<MiningPhase, true>> = { dig: true, smelt: true, escape: true };

/** What the run is doing. Kinds that can happen anywhere (track, speech, captcha) take the newest
 *  earlier history entry with a known phase; "out" when none has one. */
export function miningPhase(intent: AgentIntent, history: readonly AgentIntent[] | undefined): MiningPhase {
  const own = PHASE[intent.kind ?? ""];
  if (own) return own;
  for (let i = (history?.length ?? 0) - 1; i >= 0; i--) {
    const p = PHASE[history?.[i]?.kind ?? ""];
    if (p) return p;
  }
  return "out";
}

const ORE_KINDS: Record<string, true> = { to_stand: true, dig: true };
const ORE_TEXT = /\((\d+) ore\)/;

/** The leg's ore so far from a step's text ("Mining by 1213,2398 (153 ore)"; loop_mining dig_stand),
 *  null for kinds that don't carry it. */
export function oreTally(i: AgentIntent): number | null {
  if (!ORE_KINDS[i.kind ?? ""]) return null;
  const m = ORE_TEXT.exec(i.text);
  return m ? Number(m[1]) : null;
}

/** Leg rows start no earlier than this: the `since` of the newest history entry that isn't this
 *  run's trip (an earlier trip, another loop), or null when the history is all this trip. */
export function tripBound(intent: AgentIntent, history: readonly AgentIntent[] | undefined): number | null {
  const h = history ?? [];
  for (let i = h.length - 1; i >= 0; i--) {
    const e = h[i]!;
    if (e.loop !== "mining" || e.trip !== intent.trip) return e.since;
  }
  return null;
}

/** This trip's finished legs, oldest first: the newest leg rows with the intent's trip number (not
 *  before tripBound), leg numbers counting down to 1, each but the newest hopped on. A row that
 *  didn't hop ends a trip, so it is the newest only once the trip is done (trip_done). */
export function tripLegs(legs: readonly MiningLeg[], intent: AgentIntent, history: readonly AgentIntent[] | undefined): MiningLeg[] {
  if (typeof intent.trip !== "number") return [];
  const bound = tripBound(intent, history);
  const mine = legs
    .filter((l) => l.trip === intent.trip && l.t_start !== null && (bound === null || l.t_start >= bound))
    .sort((a, b) => b.t_start! - a.t_start!);
  const out: MiningLeg[] = [];
  for (const l of mine) {
    const prev = out[out.length - 1];
    if (l.leg === null) break;
    if (prev === undefined ? l.outcome !== "hopped" && intent.kind !== "trip_done" : l.leg !== prev.leg! - 1 || l.outcome !== "hopped") break;
    out.push(l);
    if (l.leg === 1) break;
  }
  return out.reverse();
}

/** The leg in progress's ore: the newest tally of this trip at or after `legStart` (the newest
 *  finished leg's end; null for the trip's first leg), 0 before its first dig. */
export function legOre(intent: AgentIntent, history: readonly AgentIntent[] | undefined, legStart: number | null): number {
  const seq = [...(history ?? []), intent];
  for (let i = seq.length - 1; i >= 0; i--) {
    const e = seq[i]!;
    if (e.loop !== "mining" || e.trip !== intent.trip || (legStart !== null && e.since < legStart)) break;
    const n = oreTally(e);
    if (n !== null) return n;
  }
  return 0;
}

export interface TripTally {
  ore: number;
  /** null when the leg in progress's are unknown (a viz server without open_leg) */
  smelts: number | null;
  ingotsMade: number | null;
}

/** The trip so far: its finished legs plus, while one is going (`current`), the leg in progress
 *  (its ore from the intent, its smelts and ingots from /api/jobs open_leg). */
export function tripTally(done: readonly MiningLeg[], current: { ore: number } | null, open: OpenLeg | undefined): TripTally {
  const sum = (k: "ore" | "smelts" | "ingots_made") => done.reduce((s, l) => s + l[k], 0);
  const live = current !== null;
  return {
    ore: sum("ore") + (current?.ore ?? 0),
    smelts: live && !open ? null : sum("smelts") + (live ? open!.smelts : 0),
    ingotsMade: live && !open ? null : sum("ingots_made") + (live ? open!.ingots_made : 0),
  };
}

/** The legs at `spot`, newest first by t_start (a leg without one last). */
export function spotLegs(legs: readonly MiningLeg[], spot: string): MiningLeg[] {
  return legs.filter((l) => l.spot === spot).sort((a, b) => (b.t_start ?? -Infinity) - (a.t_start ?? -Infinity));
}

export interface MetalMix {
  name: string;
  ingots: number;
  /** of all the ingots made in the legs */
  share: number;
}

/** The ingots made in `legs` by metal (metals_made), most first (ties by name), zeros dropped. */
export function metalMix(legs: readonly MiningLeg[]): MetalMix[] {
  const by = new Map<string, number>();
  for (const l of legs) for (const [name, n] of Object.entries(l.metals_made)) by.set(name, (by.get(name) ?? 0) + n);
  const rows = [...by].filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const total = rows.reduce((s, [, n]) => s + n, 0);
  return rows.map(([name, ingots]) => ({ name, ingots, share: ingots / total }));
}

/** The ore rate's trailing window, and the least field time it needs before it shows. */
export const RATE_WINDOW_S = 300;
export const RATE_MIN_S = 60;

export interface OreRate {
  /** ore dug per hour over the span */
  oreH: number;
  /** seconds the rate is measured over (up to RATE_WINDOW_S) */
  spanS: number;
}

/** Ore/hr right now, as the lumber pane's currentRate: the ore dug since the tally of `windowS` ago
 *  (or since the field run began, when that is later), over the time since then, ending at `now`.
 *  Samples are the closed history entries of this trip, each at its `until` with the leg's tally then
 *  (its text's, else the last one known: smelting, an escape or a captcha dig nothing; heading out or
 *  hopping starts a new leg at 0). A field run begins at the end of the last step away from the spot
 *  or of the travel lockout; a tally that drops (a new leg) starts over. Null away from the spot,
 *  during the lockout, without a tally or under RATE_MIN_S of samples. */
export function currentOreRate(
  intent: AgentIntent,
  history: readonly AgentIntent[] | undefined,
  now: number | null,
  windowS = RATE_WINDOW_S,
): OreRate | null {
  if (now === null || intent.kind === "lockout") return null;
  if (!FIELD_PHASES[miningPhase(intent, history)]) return null;
  let samples: [number, number][] = [];
  let lastN: number | null = null;
  for (const h of history ?? []) {
    if (h.until === undefined) continue; // the current step
    if (h.trip !== intent.trip) {
      samples = [];
      lastN = null;
      continue;
    }
    const phase = PHASE[h.kind ?? ""];
    if ((phase !== undefined && !FIELD_PHASES[phase]) || h.kind === "lockout") samples = [];
    if (phase === "out" || phase === "hop") lastN = 0;
    const n = oreTally(h) ?? lastN;
    if (n === null) continue;
    lastN = n;
    if (samples.length && n < samples[samples.length - 1]![1]) samples = [];
    samples.push([h.until, n]);
  }
  const cur = oreTally(intent) ?? lastN;
  if (cur === null) return null;
  const last = samples[samples.length - 1];
  if (last && cur < last[1]) samples = [];
  let start = samples[0];
  for (const s of samples) if (s[0] <= now - windowS) start = s;
  if (!start || now - start[0] < RATE_MIN_S) return null;
  const spanS = now - start[0];
  return { oreH: ((cur - start[1]) / spanS) * 3600, spanS };
}

/** loop_mining.py's graphics. */
export const ORE: ReadonlySet<number> = new Set([0x19b7, 0x19b8, 0x19b9, 0x19ba]);
export const INGOTS: ReadonlySet<number> = new Set([0x1bf2]);
export const PICKAXES: ReadonlySet<number> = new Set([0x0e85, 0x0e86]);

export interface MiningCarried {
  ore: number;
  /** at any depth in the backpack, the trapped pouches included */
  ingots: number;
  pickaxesWorn: number;
  pickaxesPacked: number;
}

/** What we carry now, from the live snapshot: ore and ingots (stack amounts) at any depth in the
 *  backpack; pickaxes on us (any layer) and in the backpack. Null without our serial. */
export function miningCarried(world: Snapshot | null | undefined): MiningCarried | null {
  const me = world?.self.serial;
  if (!world || !me) return null;
  const items = world.items;
  const pack = Object.entries(items).find(([, it]) => it.container === me && it.layer === LAYER_BACKPACK)?.[0];
  const inPack = (serial: HexSerial): boolean => {
    let c = items[serial]?.container;
    for (let depth = 0; pack !== undefined && c !== undefined && depth < 16; depth++) {
      if (c === pack) return true;
      c = items[c]?.container;
    }
    return false;
  };
  const out: MiningCarried = { ore: 0, ingots: 0, pickaxesWorn: 0, pickaxesPacked: 0 };
  for (const [serial, it] of Object.entries(items)) {
    const g = it.graphic ?? -1;
    if (PICKAXES.has(g) && it.container === me) out.pickaxesWorn++;
    else if (!inPack(serial)) continue;
    else if (PICKAXES.has(g)) out.pickaxesPacked++;
    else if (ORE.has(g)) out.ore += it.amount ?? 1;
    else if (INGOTS.has(g)) out.ingots += it.amount ?? 1;
  }
  return out;
}

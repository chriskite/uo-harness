// The Hunting Grounds advice on the map (docs/VISUALIZER.md §2.22): the proxy state's `grounds`
// (harness/hunting_grounds.py GroundsLog.status), what the badge says, when it turns urgent, and how the
// overlay colours the bucks. The plan itself is computed by the proxy; this only words and colours it.
import { fmtLeft } from "./fieldtimer.ts";

export type Phase = "out" | "north" | "hunt" | "return" | "done";
export type Verdict = "shoot" | "wait" | "skip";
export type Mood = "calm" | "nervous" | "fleeing";

export interface GroundsPlan {
  phase: Phase;
  /** The band chosen (index into `bands`), null when returning or done. */
  band: number | null;
  /** The recommended y range (inclusive): the chosen band and its equally good neighbours. */
  zone: [number, number] | null;
  /** The zone's mean points per buck, shallowest and deepest band. */
  zone_points?: [number, number];
  /** Trophies to take this trip, and how many would fit before the run home. */
  kills: number;
  kills_max: number;
  /** Shoot calm bucks worth at least this; null when not hunting. */
  min_points: number | null;
  /** Points still to come this run if the plan is followed (held trophies included). */
  expect: number;
}

export interface GroundsBuck {
  serial: string;
  x: number;
  y: number;
  points: number;
  /** The deer's last mood label, null when none in the last 15 s. */
  mood: Mood | null;
  dist: number;
  verdict: Verdict;
  /** Detection penalty from the wind: 30 (directly downwind of us), 15 (one octant off) or 0. */
  downwind: number;
}

export interface GroundsWeapon {
  name: string;
  range: number;
  graze: number;
  /** Where to shoot from: outside the nervous radius, inside the usable range (capped at the view). */
  shoot_from: [number, number];
  next: { name: string; at: number; range: number; graze: number } | null;
}

export interface GroundsStatus {
  started: number;
  ends: number;
  left_s: number;
  /** The Game Warden's tile. */
  lodge: [number, number];
  /** x0, y0, x1, y1 (inclusive). */
  region: [number, number, number, number];
  held: number[];
  held_points: number;
  hold_max: number;
  turned_in: number;
  score: number;
  ammo: number | null;
  /** Where the wind blows to ("south"), null before the first wind line. */
  wind: string | null;
  weapon: GroundsWeapon;
  bands: { y: [number, number]; points: number }[];
  /** Chebyshev tiles from the warden you can still run back from in time. */
  no_return_r: number;
  /** The run home from here (s, margin included) and the time to spare before it must start. */
  home_s: number | null;
  slack_s: number | null;
  warn_slack_s: number;
  plan: GroundsPlan | null;
  bucks: GroundsBuck[];
}

export interface GroundsView {
  left: string;
  headline: string;
  lines: string[];
  title: string;
  /** Holding trophies and the run home no longer fits the margin: go now. */
  urgent: boolean;
  /** Holding trophies with little time to spare, or the plan says go back. */
  warn: boolean;
}

export const VERDICT_COLORS: Record<Verdict, string> = {
  shoot: "#22c55e",
  wait: "#f59e0b",
  skip: "#6b7280",
};

const pts = (n: number) => `${n} pt${n === 1 ? "" : "s"}`;

/** Wind blowing to `wind`: stand on that side of a deer (it is then upwind of you). */
export function windAdvice(wind: string | null): string | null {
  return wind ? `wind to the ${wind}: approach deer from their ${wind} side` : null;
}

/** The badge, or null outside the Hunting Grounds. */
export function groundsView(g: GroundsStatus | null | undefined): GroundsView | null {
  if (!g) return null;
  const p = g.plan;
  const held = g.held.length;
  const slack = g.slack_s;
  const urgent = held > 0 && slack !== null && slack <= 0;
  const warn = urgent || (held > 0 && slack !== null && slack <= g.warn_slack_s) || p?.phase === "return";
  let headline = "Waiting for our position";
  if (p) {
    const zone = p.zone ? `y ${p.zone[0]}–${p.zone[1]}` : "";
    const zp = p.zone_points ? ` (~${p.zone_points[0]}${p.zone_points[1] !== p.zone_points[0] ? `–${p.zone_points[1]}` : ""} pts a buck)` : "";
    headline = {
      out: `Run south to ${zone}${zp}`,
      north: `Work north to ${zone}${zp}`,
      hunt: `Hunt here: calm bucks ≥ ${pts(p.min_points ?? 2)}`,
      return:
        held >= g.hold_max
          ? `${held} trophies: run back to the warden`
          : `Head back now: ${held} ${held === 1 ? "trophy" : "trophies"} (${pts(g.held_points)})`,
      done: "No time for another trophy",
    }[p.phase];
  }
  const lines: string[] = [];
  if (p && p.min_points !== null && p.phase !== "hunt") lines.push(`shoot calm bucks ≥ ${pts(p.min_points)}`);
  if (p && p.min_points !== null) {
    const [a, b] = g.weapon.shoot_from;
    lines.push(b >= 18 ? `shoot from where you see them (≤ ${b} tiles)` : `shoot from ${a}–${b} tiles`);
  }
  if (held > 0 && slack !== null) {
    const home = Math.round(g.home_s ?? 0);
    lines.push(slack > 0 ? `head back within ${fmtLeft(slack)} (run home ~${home} s)` : `head back NOW: the run home takes ~${home} s, ${Math.ceil(g.left_s)} s left`);
  }
  if (p && p.kills > 0) lines.push(`this trip: ${p.kills} more · ~${Math.round(p.expect)} pts to come this run`);
  lines.push(`held ${held}/${g.hold_max} (${pts(g.held_points)}) · score ${g.score}${g.ammo !== null ? ` · ammo ${g.ammo}` : ""}`);
  const w = g.weapon;
  const next = w.next ? ` · ${w.next.name} at ${w.next.at} (${w.next.at - g.turned_in} more turned in)` : "";
  lines.push(`${w.name}: range ${w.range}, graze ${Math.round(w.graze * 100)}%${next}`);
  const wind = windAdvice(g.wind);
  if (wind) lines.push(wind);
  const ends = new Date(g.ends * 1000).toLocaleTimeString([], { hour12: false });
  return {
    left: fmtLeft(g.left_s),
    headline,
    lines,
    title: `Hunting Grounds: the permit expires at ${ends}; held trophies are lost then. Plan from docs/research/HUNTING_GROUNDS.md §4`,
    urgent,
    warn,
  };
}

/** A buck's map caption: its points, mood and, when the wind carries our scent to it, the penalty. */
export function buckCaption(b: GroundsBuck): string {
  const mood = b.mood ?? "?";
  const wind = b.downwind ? ` · scent +${b.downwind}%` : "";
  return `${b.points} ${mood}${wind}`;
}

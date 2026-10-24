// A mobile's active statuses at t (the map's tags over each mobile, the inspector's list).
import type { MobRec, WorldRec } from "./types.ts";

export interface StatusTag {
  label: string;
  color: string;
  /** When it ends (sim s). */
  until: number;
  /** What it means, for titles. */
  what: string;
}

/** Statuses whose timer is past t; the player also gets the world's paralyze (PZ) and hamstring (HS). */
export function statusTags(m: MobRec, w: WorldRec | null, t: number): StatusTag[] {
  const out: StatusTag[] = [];
  const add = (label: string, color: string, until: number | null | undefined, what: string) => {
    if (until && until > t) out.push({ label, color, until, what });
  };
  add("PO", "#22c55e", m.po, "poisoned");
  add("BL", "#b91c1c", m.bl, "bleeding");
  const ds = (m.ds ?? []).filter(([last]) => last > t);
  if (ds.length) add(`DS×${ds.length}`, "#a3a32a", Math.max(...ds.map(([last]) => last)), "diseased");
  add("DC", "#a855f7", m.dc, "discorded");
  add("PC", "#38bdf8", m.ca, "peacemade (calmed)");
  add("PV", "#f97316", m.pv?.[1], "provoked");
  add("HD", "#eab308", m.hd, "held (transfixed)");
  add("HI", "#64748b", m.hi, "hidden");
  add("IM", "#e5e7eb", m.im, "immune to bard skills");
  if (w && m.kind === "player") {
    add("PZ", "#facc15", w.par, "paralyzed");
    add("HS", "#fb7185", w.ham, "hamstrung");
  }
  return out;
}

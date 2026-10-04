// Buff names: the server's title when it sent one, else its cliloc rendered from the
// client's Cliloc.enu (GET /api/cliloc; most Outlands buffs come with an empty title
// and a cliloc, e.g. icon 138 = cliloc 1044416 "Magic Reflection"), else the icon id.
import type { Buff } from "./types.ts";

/** Cliloc numbers looked up so far: text, or null when Cliloc.enu doesn't have it (or is missing). */
const known = new Map<number, string | null>();

export function buffName(b: Buff, texts: ReadonlyMap<number, string | null>): string {
  return b.title || (b.cliloc !== undefined && texts.get(b.cliloc)) || `buff ${b.icon_id}`;
}

/** The buffs' clilocs that name them (untitled buffs only), sorted, unique. */
export function buffClilocs(buffs: readonly Buff[]): number[] {
  const out = new Set<number>();
  for (const b of buffs) if (!b.title && typeof b.cliloc === "number") out.add(b.cliloc);
  return [...out].sort((a, b) => a - b);
}

/** Look up the numbers not seen yet; a network error leaves them unknown, so the next call retries. */
export async function fetchClilocTexts(numbers: readonly number[]): Promise<ReadonlyMap<number, string | null>> {
  const missing = numbers.filter((n) => !known.has(n));
  if (missing.length > 0) {
    try {
      const r = await fetch(`api/cliloc?n=${missing.join(",")}`);
      const j = r.ok ? ((await r.json()) as { texts: Record<string, string> }) : null;
      for (const n of missing) known.set(n, j?.texts[String(n)] ?? null);
    } catch {
      // backend unreachable: retried on the next change
    }
  }
  return known;
}

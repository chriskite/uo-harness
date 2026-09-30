// What the paperdoll image depends on, as a cache key for /api/paperdoll.png:
// body, skin hue, and each worn item (layer, graphic, hue). The server renders
// from the current state; the key only makes the browser refetch when it changes.
import type { Snapshot } from "./types.ts";

const LAYER_MOUNT = 0x19;
const FIRST_NON_WORN_LAYER = 0x1a; // 0x1A-0x1D: vendor containers, bank box

export function paperdollKey(world: Snapshot | undefined): string | null {
  const me = world?.self;
  const body = me?.body ?? me?.stats?.graphic;
  if (!me?.serial || body === undefined) return null;
  const selfNum = parseInt(me.serial, 16);
  const worn: string[] = [];
  for (const it of Object.values(world?.items ?? {})) {
    const layer = it.layer;
    if (it.container === undefined || parseInt(it.container, 16) !== selfNum) continue;
    if (!layer || layer === LAYER_MOUNT || layer >= FIRST_NON_WORN_LAYER || it.graphic === undefined) continue;
    worn.push(`${layer}.${it.graphic}.${it.hue ?? 0}`);
  }
  worn.sort();
  return `${body}-${me.stats?.hue ?? 0}-${worn.join("_")}`;
}

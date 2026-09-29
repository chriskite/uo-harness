// Serial normalization (docs/VISUALIZER.md §3): the snapshot keys serials as
// '0x%08X' strings while events carry ints. Everything in the UI uses HexSerial.
import type { HexSerial, Item, Mobile, Snapshot } from "./types.ts";

/** Normalize an int, decimal string or hex string (any case/padding) to '0x%08X'. */
export function toHex(serial: unknown): HexSerial | null {
  let n: number;
  if (typeof serial === "number") {
    n = serial;
  } else if (typeof serial === "string") {
    const s = serial.trim();
    if (/^0x[0-9a-f]+$/i.test(s)) n = parseInt(s.slice(2), 16);
    else if (/^\d+$/.test(s)) n = parseInt(s, 10);
    else return null;
  } else {
    return null;
  }
  if (!Number.isInteger(n) || n < 0 || n > 0xffffffff) return null;
  return "0x" + n.toString(16).toUpperCase().padStart(8, "0");
}

/** Event fields that hold serials (runtime.py `_emit` vocabulary). */
export const SERIAL_FIELDS: Record<string, true> = {
  serial: true,
  attacker: true,
  defender: true,
  container: true,
  parent: true,
  item: true,
  self_serial: true,
};

export type EntityRef =
  | { kind: "mobile"; serial: HexSerial; mobile: Mobile; isSelf: boolean }
  | { kind: "item"; serial: HexSerial; item: Item }
  | { kind: "name"; serial: HexSerial; name: string };

/** Entity lookup with the §4 precedence: mobiles -> items -> names-only. */
export function lookupEntity(world: Snapshot | null | undefined, serial: unknown): EntityRef | null {
  const hex = toHex(serial);
  if (hex === null || !world) return null;
  const mobile = world.mobiles[hex];
  if (mobile) return { kind: "mobile", serial: hex, mobile, isSelf: world.self.serial === hex };
  const item = world.items[hex];
  if (item) return { kind: "item", serial: hex, item };
  const name = world.names[hex];
  if (name !== undefined) return { kind: "name", serial: hex, name };
  return null;
}

/** Best display name for a serial: self name, entity name, names map, else null. */
export function displayName(world: Snapshot | null | undefined, serial: unknown): string | null {
  const hex = toHex(serial);
  if (hex === null || !world) return null;
  if (world.self.serial === hex && world.self.name) return world.self.name;
  const ref = lookupEntity(world, hex);
  if (!ref) return null;
  if (ref.kind === "name") return ref.name;
  const name = ref.kind === "mobile" ? ref.mobile.name : ref.item.name;
  return name ?? world.names[hex] ?? null;
}

/** '0x' + upper-case hex zero-padded to `width` digits (packet ids, graphics, layers). */
export function hex(n: number, width: number): string {
  return "0x" + n.toString(16).toUpperCase().padStart(width, "0");
}

// Display vocabularies and formatters shared by panels.

/** Notoriety -> colour (UO notoriety 1..7). */
export const NOTORIETY: Record<number, { name: string; color: string }> = {
  1: { name: "innocent", color: "#4aa8ff" },
  2: { name: "ally", color: "#4ade80" },
  3: { name: "attackable", color: "#a3a3a3" },
  4: { name: "criminal", color: "#9ca3af" },
  5: { name: "enemy", color: "#fb923c" },
  6: { name: "murderer", color: "#ef4444" },
  7: { name: "invulnerable", color: "#facc15" },
};

export const UNKNOWN_NOTORIETY_COLOR = "#c084fc";

/** Equipment layers (UO layer ids). 0x15 backpack and 0x1D bank box are the containers. */
export const LAYER_NAMES: Record<number, string> = {
  0x01: "one-handed",
  0x02: "two-handed",
  0x03: "shoes",
  0x04: "pants",
  0x05: "shirt",
  0x06: "helm",
  0x07: "gloves",
  0x08: "ring",
  0x09: "talisman",
  0x0a: "neck",
  0x0b: "hair",
  0x0c: "waist",
  0x0d: "inner torso",
  0x0e: "bracelet",
  0x0f: "face",
  0x10: "facial hair",
  0x11: "middle torso",
  0x12: "earrings",
  0x13: "arms",
  0x14: "cloak",
  0x15: "backpack",
  0x16: "outer torso",
  0x17: "outer legs",
  0x18: "inner legs",
  0x19: "mount",
  0x1a: "shop buy",
  0x1b: "shop restock",
  0x1c: "shop sell",
  0x1d: "bank box",
};

/** C2S packet ids seen from the agent (proxy `c2s` events). */
export const C2S_NAMES: Record<string, string> = {
  "0x02": "walk",
  "0x05": "attack",
  "0x06": "double-click",
  "0x07": "lift item",
  "0x08": "drop item",
  "0x09": "single-click",
  "0x12": "text command",
  "0x13": "equip",
  "0x22": "resync",
  "0x34": "status query",
  "0x6C": "target response",
  "0x72": "war mode",
  "0x73": "ping",
  "0x98": "name request",
  "0xAD": "speech",
  "0xB1": "gump response",
  "0xBF": "general info",
  "0xD7": "AoS command",
};

/** Facing 0..7 = N NE E SE S SW W NW. */
export const DIR_NAMES = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"] as const;

/** Wall-clock seconds -> local HH:MM:SS.mmm. */
export function fmtTime(t: number): string {
  const d = new Date(t * 1000);
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`;
}

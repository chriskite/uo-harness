// The character the viz shows when several are logged in through the proxy (docs/VISUALIZER.md):
// a serial selector ("0x0020F127") sent as `char` on the per-character routes. Kept in
// module state, persisted in localStorage; null = let the server pick (its only session).
import { useSyncExternalStore } from "react";

/** One of the proxy's logged-in characters (GET /api/sessions). */
export type Session = { tag: string; serial: string | null; name: string | null };

const STORE_KEY = "viz.char";
const storage = typeof localStorage === "undefined" ? null : localStorage;

let current: string | null = storage?.getItem(STORE_KEY) || null;
const listeners = new Set<() => void>();

export function getChar(): string | null {
  return current;
}

export function setChar(c: string | null): void {
  if (c === current) return;
  current = c;
  if (c === null) storage?.removeItem(STORE_KEY);
  else storage?.setItem(STORE_KEY, c);
  for (const l of listeners) l();
}

export function subscribeChar(l: () => void): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

export function useChar(): string | null {
  return useSyncExternalStore(subscribeChar, getChar);
}

/** `url` with `char=<current>` appended (none when no character is chosen). */
export function withChar(url: string, char: string | null = current): string {
  if (!char) return url;
  return `${url}${url.includes("?") ? "&" : "?"}char=${encodeURIComponent(char)}`;
}

/** The character to show given the proxy's sessions: the current one while it is logged
 *  in, else the first identified session, else unchanged (nothing to choose from). */
export function pickChar(current: string | null, sessions: readonly Session[]): string | null {
  const ids = sessions.map((s) => s.serial).filter((s): s is string => s !== null);
  if (current !== null && ids.includes(current)) return current;
  return ids.length > 0 ? ids[0]! : current;
}

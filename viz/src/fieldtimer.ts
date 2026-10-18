// The Field of Souls countdown on the map (docs/VISUALIZER.md §2.18): the proxy state's `field`
// (harness/fos_field.py FieldLog.status), what the badge says and when it turns urgent.

/** A field session while we're in it: named = when the server named our spirit (the clock's start). */
export interface FieldClock {
  instance: string;
  spirit: string;
  named: number;
  /** When the server ejects us (epoch s). */
  ends: number;
  /** Seconds left as of the state response. */
  left_s: number;
  /** The proxy's warning (a toll) comes this long before the end. */
  warn_s: number;
}

export interface FieldTimerView {
  left: string;
  text: string;
  title: string;
  urgent: boolean;
}

/** Seconds -> "14:53", "0:09" (whole seconds, rounded up so 0:00 only shows at the end). */
export function fmtLeft(s: number): string {
  const t = Math.max(0, Math.ceil(s));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** The badge, or null outside the field. */
export function fieldTimer(f: FieldClock | null | undefined): FieldTimerView | null {
  if (!f) return null;
  const left = fmtLeft(f.left_s);
  const ends = new Date(f.ends * 1000).toLocaleTimeString([], { hour12: false });
  return {
    left,
    text: `${cap(f.instance)} · spirit ${f.spirit}`,
    title: `Field of Souls (${f.instance}): ejected at ${ends}, ${fmtLeft(f.ends - f.named)} after the server named your spirit; a toll ${f.warn_s} s before`,
    urgent: f.left_s <= f.warn_s,
  };
}

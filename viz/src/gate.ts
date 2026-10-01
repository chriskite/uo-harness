// Agent-gate display rules (docs/VISUALIZER.md §2.2): badge per
// state and which controls the header offers.
import type { Gate, GateState } from "./types.ts";

export const GATE_BADGE: Record<GateState, { label: string; kind: "ok" | "warn" | "bad" | "info" }> = {
  running: { label: "agent running", kind: "ok" },
  paused: { label: "agent paused", kind: "warn" },
  break: { label: "agent on break", kind: "info" },
  break_due: { label: "break due", kind: "warn" },
  budget_exhausted: { label: "daily cap reached", kind: "warn" },
  killed: { label: "agent KILLED", kind: "bad" },
};

export interface GateButtons {
  /** The Pause/Resume toggle: `resume` while manually paused (it clears only that). */
  toggle: "pause" | "resume";
  toggleEnabled: boolean;
  killEnabled: boolean;
}

/** Controls for `gate`, or null when none are offered (replay, or no gate known).
 * Killed disables everything: resume errors while killed and rearm is CLI-only.
 * A break or the daily cap does not block pausing or killing on top of it.
 * `busy` (a request in flight) disables both buttons. */
export function gateControls(gate: Gate | null | undefined, live: boolean, busy = false): GateButtons | null {
  if (!live || !gate) return null;
  const toggle = gate.paused ? "resume" : "pause";
  if (gate.killed) return { toggle, toggleEnabled: false, killEnabled: false };
  return { toggle, toggleEnabled: !busy, killEnabled: !busy };
}

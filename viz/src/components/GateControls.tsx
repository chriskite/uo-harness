import { useEffect, useState } from "react";
import { postGate } from "../api.ts";
import { fmtClock, fmtDuration, fmtHM } from "../format.ts";
import { GATE_BADGE, gateControls } from "../gate.ts";
import { vizStore } from "../store.ts";
import type { Gate, GateAction } from "../types.ts";
import { Badge } from "./common.tsx";

const KILL_CONFIRM_MS = 5_000;
const REARM_CMD = "python harness/agent_gate.py rearm";

/** Agent gate in the header (live only; nothing in replay): state + reason,
 * Pause/Resume, Kill with a confirm step, next forced break (agent-active
 * time), break end, daily usage. */
export function GateControls({ gate, live }: { gate: Gate | undefined; live: boolean }) {
  const [busy, setBusy] = useState(false);
  const [confirmKill, setConfirmKill] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!confirmKill) return;
    const t = setTimeout(() => setConfirmKill(false), KILL_CONFIRM_MS);
    return () => clearTimeout(t);
  }, [confirmKill]);

  if (!live) return null;
  const ctl = gateControls(gate, live, busy);
  if (!gate || !ctl) {
    return (
      <span className="gate">
        <Badge kind="dim" title="the proxy's state responses carry no gate (proxy down or pre-gate proxy)">
          gate ?
        </Badge>
      </span>
    );
  }

  const send = (action: GateAction) => {
    setBusy(true);
    setConfirmKill(false);
    postGate(action)
      .then(
        (g) => {
          if (g) vizStore.setGate(g);
          setError(null);
        },
        (e: unknown) => setError(e instanceof Error ? e.message : String(e)),
      )
      .finally(() => setBusy(false));
  };

  const badge = GATE_BADGE[gate.state];
  return (
    <span className="gate">
      <Badge kind={badge.kind} title={gate.reason ?? "agent injections pass"}>
        {badge.label}
      </Badge>
      {gate.reason && (
        <span className="gate-reason dim" title={gate.reason}>
          {gate.reason}
        </span>
      )}
      <button
        type="button"
        disabled={!ctl.toggleEnabled}
        onClick={() => send(ctl.toggle)}
        title={
          ctl.toggle === "resume"
            ? "clear the manual pause (a break or the daily cap still applies)"
            : "block agent injections until resumed"
        }
      >
        {ctl.toggle === "resume" ? "▶ resume" : "⏸ pause"}
      </button>
      {gate.killed ? (
        <span className="mono dim" title="the viz cannot rearm">
          rearm is CLI-only: <code>{REARM_CMD}</code>
        </span>
      ) : confirmKill ? (
        <>
          <button type="button" className="danger" disabled={!ctl.killEnabled} onClick={() => send("kill")}>
            confirm kill
          </button>
          <button type="button" onClick={() => setConfirmKill(false)}>
            cancel
          </button>
        </>
      ) : (
        <button
          type="button"
          className="danger"
          disabled={!ctl.killEnabled}
          onClick={() => setConfirmKill(true)}
          title={`block the agent until rearmed from the CLI (${REARM_CMD})`}
        >
          ■ kill
        </button>
      )}
      {gate.break_until !== null ? (
        <span className="mono" title="scheduled break (wall clock)">
          break until {fmtClock(gate.break_until)} ({fmtDuration(gate.break_until - gate.now)} left)
        </span>
      ) : gate.break_starts_in_s != null ? (
        <span className="mono" title="the break is due: it starts by itself when this runs out, unless the Seer starts it earlier (ctl break)">
          break starts in {fmtDuration(gate.break_starts_in_s)}
        </span>
      ) : gate.next_break_in_s !== null ? (
        <span className="mono dim" title="agent-active time left before the next forced break">
          next break in {fmtDuration(gate.next_break_in_s)} active
        </span>
      ) : null}
      <span className="mono dim" title={`agent-active time on ${gate.day} against the daily cap`}>
        today {fmtHM(gate.active_today_s)} / {fmtHM(gate.daily_cap_s)}
      </span>
      {error && <span className="error">{error}</span>}
    </span>
  );
}

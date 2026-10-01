import { useEffect, useState } from "react";
import { type CaptchaMode, fetchCaptchaMode, postCaptchaMode } from "../api.ts";

const POLL_MS = 5_000;
const MODES: { mode: CaptchaMode; label: string; title: string }[] = [
  { mode: "human", label: "human", title: "the runner pauses and beeps until you solve the captcha in the client" },
  {
    mode: "auto",
    label: "auto",
    title: "the runner reads the digits from the gump layout and answers; unreadable or rejected → pause + beep",
  },
];

/** Who answers the harvest captcha (memory store, read by the runner at every
 * captcha). Polled so a change from another tab shows up. */
export function CaptchaToggle() {
  const [mode, setMode] = useState<CaptchaMode | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetchCaptchaMode().then(
        (m) => {
          if (!alive) return;
          setMode(m);
          setError(null);
        },
        (e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)),
      );
    load();
    const t = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const send = (m: CaptchaMode) => {
    setBusy(true);
    postCaptchaMode(m)
      .then(
        (got) => {
          setMode(got);
          setError(null);
        },
        (e: unknown) => setError(e instanceof Error ? e.message : String(e)),
      )
      .finally(() => setBusy(false));
  };

  return (
    <span className="gate" title="who answers the harvest captcha">
      <span className="dim">captcha</span>
      <span className="page-switch" role="group" aria-label="captcha mode">
        {MODES.map((m) => (
          <button
            key={m.mode}
            type="button"
            className={m.mode === mode ? "active" : undefined}
            aria-pressed={m.mode === mode}
            disabled={busy || mode === null}
            title={m.title}
            onClick={() => m.mode !== mode && send(m.mode)}
          >
            {m.label}
          </button>
        ))}
      </span>
      {error && <span className="error">{error}</span>}
    </span>
  );
}

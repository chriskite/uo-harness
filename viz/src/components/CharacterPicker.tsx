import { useEffect, useState } from "react";
import { fetchSessions } from "../api.ts";
import { getChar, pickChar, type Session, setChar, useChar } from "../character.ts";

const POLL_MS = 5_000;

/** Which logged-in character the viz shows (live). Polls the proxy's sessions: keeps
 * the chosen character while it is online, else switches to the first one. */
export function CharacterPicker() {
  const char = useChar();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetchSessions().then(
        (s) => {
          if (!alive) return;
          setSessions(s);
          setError(null);
          setChar(pickChar(getChar(), s));
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

  if (sessions.length === 0) return error ? <span className="error">{error}</span> : null;
  return (
    <span className="gate" title="the character this viz shows (proxy sessions)">
      <span className="dim">character</span>
      <select value={char ?? ""} onChange={(e) => setChar(e.target.value || null)} aria-label="character">
        {char === null && <option value="">choose…</option>}
        {sessions.map((s) => (
          <option key={s.tag} value={s.serial ?? ""} disabled={s.serial === null}>
            {s.name ?? "unidentified"}
          </option>
        ))}
      </select>
      {error && <span className="error">{error}</span>}
    </span>
  );
}

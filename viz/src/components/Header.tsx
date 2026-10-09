import { useState } from "react";
import { postPlayback } from "../api.ts";
import type { VizSnapshot } from "../store.ts";
import type { PlaybackAction } from "../types.ts";
import { Badge } from "./common.tsx";
import { CaptchaToggle } from "./CaptchaToggle.tsx";
import { CharacterPicker } from "./CharacterPicker.tsx";
import { GateControls } from "./GateControls.tsx";

const RATES = [0.25, 0.5, 1, 2, 4, 8, 16, 32];

export const PAGES = ["Live", "Jobs", "Nystul"] as const;
export type Page = (typeof PAGES)[number];

export function Header({ viz, page, onPage }: { viz: VizSnapshot; page: Page; onPage: (p: Page) => void }) {
  const info = viz.state?.viz;
  const pb = info?.playback ?? null;
  const [error, setError] = useState<string | null>(null);

  const send = (a: PlaybackAction) => {
    postPlayback(a).then(
      () => setError(null),
      (e: unknown) => setError(String(e)),
    );
  };

  return (
    <header className="header">
      <span className="title">uo-harness viz</span>
      <nav className="page-switch" aria-label="page">
        {PAGES.map((p) => (
          <button key={p} type="button" className={p === page ? "active" : undefined} onClick={() => onPage(p)}>
            {p}
          </button>
        ))}
      </nav>
      {info?.mode === "live" && <CharacterPicker />}
      {!info ? (
        <Badge kind="dim">no state</Badge>
      ) : info.mode === "live" ? (
        <Badge kind="ok">LIVE</Badge>
      ) : (
        <>
          <Badge kind="info">REPLAY {info.session ?? "?"}</Badge>
          {info.order === "exact" ? (
            <Badge kind="ok" title="jsonl interleave recovered row by row">
              exact order
            </Badge>
          ) : (
            <Badge kind="warn" title="canonical order (all C2S then all S2C): movement truth is unreliable">
              approx order: movement truth unreliable
            </Badge>
          )}
        </>
      )}
      <span className="conn" title="SSE stream to viz_server">
        <span className={viz.connected ? "dot dot-ok" : "dot dot-bad"} /> stream
      </span>
      {info && (
        <span className="conn" title={info.mode === "live" ? "viz_server polling the proxy state port" : "replay feeder"}>
          <span className={info.connected ? "dot dot-ok" : "dot dot-bad"} /> {info.mode === "live" ? "proxy" : "feeder"}
        </span>
      )}
      {info?.mode === "replay" && pb && (
        <span className="playback">
          <button type="button" onClick={() => send(pb.playing ? { action: "pause" } : { action: "play" })}>
            {pb.playing ? "⏸ pause" : "▶ play"}
          </button>
          <button type="button" disabled={pb.playing} onClick={() => send({ action: "step" })}>
            ⏭ step
          </button>
          <label>
            rate{" "}
            <select value={pb.rate} onChange={(e) => send({ action: "rate", rate: Number(e.target.value) })}>
              {(RATES.includes(pb.rate) ? RATES : [...RATES, pb.rate].sort((a, b) => a - b)).map((r) => (
                <option key={r} value={r}>
                  {r}×
                </option>
              ))}
            </select>
          </label>
          <progress max={Math.max(pb.total, 1)} value={pb.position} />
          <span className="mono">
            {pb.position}/{pb.total}
          </span>
        </span>
      )}
      {info && <GateControls gate={viz.state?.gate} live={info.mode === "live"} />}
      <CaptchaToggle />
      {error && <span className="error">{error}</span>}
      <span className="spacer" />
      {viz.state && (
        <span className="mono dim header-stats" title="next event seq · world model errors (Diagnostics panel)">
          next {viz.state.next} · world_errors {viz.state.world_errors}
        </span>
      )}
    </header>
  );
}

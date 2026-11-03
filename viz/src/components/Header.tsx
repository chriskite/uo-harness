import { useRef, useState } from "react";
import { postPlayback } from "../api.ts";
import { fmtClock, fmtDuration, parseSeekTime } from "../format.ts";
import type { VizSnapshot } from "../store.ts";
import type { Playback, PlaybackAction } from "../types.ts";
import { Badge } from "./common.tsx";
import { CaptchaToggle } from "./CaptchaToggle.tsx";
import { CharacterPicker } from "./CharacterPicker.tsx";
import { GateControls } from "./GateControls.tsx";

const RATES = [0.25, 0.5, 1, 2, 4, 8, 16, 32];

export const PAGES = ["Live", "Jobs", "Nystul", "Razor", "Sim"] as const;
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
          <Seek pb={pb} send={send} onBad={setError} />
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

/** Replay seeking: a slider over the capture (seeks on release) and the session clock as an editable
 * time (a time of day or +elapsed, Enter seeks; format.parseSeekTime). */
function Seek({ pb, send, onBad }: { pb: Playback; send: (a: PlaybackAction) => void; onBad: (e: string | null) => void }) {
  // the slider's value while dragged (a ref too: the release handler runs before the last change renders)
  const [drag, setDrag] = useState<number | null>(null);
  const dragRef = useRef<number | null>(null);
  const [text, setText] = useState<string | null>(null); // the time field while edited
  const shown = drag ?? pb.seeking ?? pb.t;

  const commitDrag = () => {
    const t = dragRef.current;
    if (t === null) return;
    dragRef.current = null;
    setDrag(null);
    send({ action: "seek", t });
  };
  const commitText = (el: HTMLInputElement) => {
    const t = parseSeekTime(el.value, pb.t0, pb.t_end);
    if (t === null) {
      onBad(`"${el.value}" is not a time in this session (${fmtClock(pb.t0)}–${fmtClock(pb.t_end)}, or +elapsed)`);
      return;
    }
    send({ action: "seek", t });
    setText(null);
    el.blur();
  };

  return (
    <>
      <input
        type="range"
        className="seek"
        min={pb.t0}
        max={pb.t_end}
        step={1}
        value={shown}
        title={`${fmtDuration(shown - pb.t0)} of ${fmtDuration(pb.t_end - pb.t0)} (item ${pb.position}/${pb.total}). Seek: drag or click, then release; arrow keys 1 s`}
        onChange={(e) => {
          dragRef.current = Number(e.target.value);
          setDrag(dragRef.current);
        }}
        onPointerUp={commitDrag}
        onKeyUp={commitDrag}
        onBlur={commitDrag}
      />
      <input
        className={pb.seeking !== null ? "mono seek-time seeking" : "mono seek-time"}
        value={text ?? fmtClock(shown)}
        title={`${pb.seeking !== null ? "seeking… " : ""}session time: type a time of day (14:05 or 14:05:30) or +elapsed (+12:30), Enter seeks, Esc cancels`}
        onFocus={(e) => {
          setText(fmtClock(shown));
          e.target.select();
        }}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commitText(e.currentTarget);
          else if (e.key === "Escape") e.currentTarget.blur();
        }}
        onBlur={() => setText(null)}
      />
    </>
  );
}

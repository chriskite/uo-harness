import { useEffect, useState } from "react";
import type { VizSnapshot } from "../store.ts";
import { Panel } from "./common.tsx";

const STORE_KEY = "uo-viz-live";
const ZOOMS: Record<number, string> = { 1: "wide", 2: "medium", 3: "close" };
/** Served frame width when the live view fills the centre (the side panel takes the server's 640). */
const BIG_WIDTH = 1280;

interface LivePrefs {
  on: boolean;
  zoom: number;
}

function loadPrefs(): LivePrefs {
  try {
    const p = JSON.parse(localStorage.getItem(STORE_KEY) ?? "{}") as Partial<LivePrefs>;
    return { on: p.on === true, zoom: p.zoom && ZOOMS[p.zoom] ? p.zoom : 2 };
  } catch {
    return { on: false, zoom: 2 };
  }
}

/** The character as the game shows it: a cropped stream of the game window
 * (GET /api/live.mjpeg; harness/liveview.py). Off by default; the server only
 * captures while the stream is open. Live mode only. `big` fills the centre
 * instead of the side; `onSwap` adds the button that swaps it with the map. */
export function LivePanel({ viz, big = false, onSwap }: { viz: VizSnapshot; big?: boolean; onSwap?: () => void }) {
  const [prefs, setPrefs] = useState<LivePrefs>(loadPrefs);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => localStorage.setItem(STORE_KEY, JSON.stringify(prefs)), [prefs]);
  if (viz.state?.viz?.mode !== "live") return null;

  const src = `api/live.mjpeg?zoom=${prefs.zoom}&fps=6${big ? `&w=${BIG_WIDTH}` : ""}&a=${attempt}`;
  const onError = () => {
    fetch(`api/live.jpg?zoom=${prefs.zoom}`)
      .then((r) => (r.ok ? null : r.json()))
      .then((j: { error?: string } | null) => setError(j?.error ?? "the stream stopped"))
      .catch(() => setError("the viz server didn't answer"));
  };
  const extra = (
    <span className="live-controls">
      {prefs.on && (
        <>
          <select value={prefs.zoom} onChange={(e) => setPrefs({ ...prefs, zoom: Number(e.target.value) })} title="zoom">
            {Object.entries(ZOOMS).map(([z, label]) => (
              <option key={z} value={z}>
                {label}
              </option>
            ))}
          </select>
          <a href={`api/live.mjpeg?zoom=${prefs.zoom}&fps=8&w=${BIG_WIDTH}`} target="_blank" rel="noreferrer" title="open larger in a new tab">
            ⤢
          </a>
        </>
      )}
      {onSwap && (
        <button type="button" title="swap the map and the live view" onClick={onSwap}>
          ⇄ swap
        </button>
      )}
      <button
        className={prefs.on ? "on" : ""}
        onClick={() => {
          setError(null);
          setPrefs({ ...prefs, on: !prefs.on });
        }}
      >
        {prefs.on ? "stop" : "watch"}
      </button>
    </span>
  );
  return (
    <Panel title="Live view" className={big ? "live live-main" : "live"} extra={extra}>
      {!prefs.on ? (
        <span className="dim">the game window around the character, streamed while you watch</span>
      ) : error ? (
        <div className="live-error">
          <span className="dim">{error}</span>{" "}
          <button
            onClick={() => {
              setError(null);
              setAttempt((a) => a + 1);
            }}
          >
            retry
          </button>
        </div>
      ) : (
        <img key={src} className="live-img" src={src} alt="live view of the character" onError={onError} />
      )}
    </Panel>
  );
}

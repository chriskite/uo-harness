// The Sim page (#sim?trace=<name>&t=<s>): replays a recorded simulator run (harness/sim/run.py or a
// fight*.py with --trace) with scrubbing: the map in the centre, the inspector and runner panel on the
// right, the timeline and damage charts below. The `run…` drawer starts sim jobs and loads their traces.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchTrace, listTraces, type TraceList } from "../sim/api.ts";
import { MARKER_ROWS, markers as buildMarkers, type MarkerRow } from "../sim/markers.ts";
import { effectsAt, parseTrace, type Trace } from "../sim/trace.ts";
import { Badge, Panel } from "./common.tsx";
import { SimDamageCharts } from "./SimDamageCharts.tsx";
import { SimInspector } from "./SimInspector.tsx";
import { SimMap } from "./SimMap.tsx";
import { SimRunnerPanel } from "./SimRunnerPanel.tsx";
import { SimRunPanel, useSimJobs } from "./SimRunPanel.tsx";
import { SimTimeline } from "./SimTimeline.tsx";

/** The hash's trace= and t= (the page's own part of #sim?…). */
function hashParams(): { trace: string | null; t: number | null } {
  const [path = "", query = ""] = location.hash.split("?", 2);
  if (path !== "#sim") return { trace: null, t: null };
  const q = new URLSearchParams(query);
  const t = Number(q.get("t"));
  return { trace: q.get("trace"), t: q.has("t") && Number.isFinite(t) ? t : null };
}

const fmtSize = (n: number) => (n >= 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} kB`);

const isTyping = (el: EventTarget | null) => el instanceof HTMLElement && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName);

export function SimPage() {
  const initial = useRef(hashParams());
  const [list, setList] = useState<TraceList | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [name, setName] = useState<string | null>(initial.current.trace);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [status, setStatus] = useState("");
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [selected, setSelected] = useState<number | null>(null);
  const [follow, setFollow] = useState(true);
  const [rows, setRows] = useState<Set<MarkerRow>>(() => new Set(MARKER_ROWS));
  const [only, setOnly] = useState(false);
  const tRef = useRef(t);
  tRef.current = t;

  const refresh = useCallback(() => {
    listTraces().then(
      (l) => {
        setList(l);
        setListError(null);
        setName((cur) => cur ?? l.traces[0]?.name ?? null);
      },
      (e: unknown) => setListError(String(e)),
    );
  }, []);
  useEffect(refresh, [refresh]);

  // Sim jobs: a job's end refreshes the trace list; a run started here that wrote one trace loads it.
  const [runOpen, setRunOpen] = useState(false);
  const sim = useSimJobs(runOpen, {
    ended: (job, mine) => {
      const only = job.traces.length === 1 ? job.traces[0] : undefined;
      if (mine && job.status === "done" && only) setName(only);
    },
    traces: refresh,
  });
  const loadJobTrace = useCallback((n: string) => {
    setName(n);
    setRunOpen(false);
  }, []);

  // Load the picked trace; the hash's t= applies to the first one only.
  useEffect(() => {
    if (!name) return;
    let live = true;
    setPlaying(false);
    setStatus(`loading ${name}…`);
    fetchTrace(name)
      .then((text) => {
        if (!live) return;
        setStatus(`parsing ${fmtSize(text.length)}…`);
        const tr = parseTrace(text);
        if (!live) return;
        const t0 = initial.current.t;
        initial.current.t = null;
        setTrace(tr);
        setT(t0 !== null ? Math.min(tr.t1, Math.max(tr.t0, t0)) : tr.t0);
        setSelected(null);
        setStatus(`${tr.keys.length} keyframes · ${tr.deltas.length} ticks · ${tr.events.length} events${tr.end ? "" : " · no end line (run cut short?)"}`);
      })
      .catch((e: unknown) => {
        if (live) setStatus(`failed: ${String(e)}`);
      });
    return () => {
      live = false;
    };
  }, [name]);

  const seek = useCallback(
    (to: number) => {
      if (!trace) return;
      setT(Math.min(trace.t1, Math.max(trace.t0, to)));
    },
    [trace],
  );

  // Playback: advance t by the frame's wall time × rate; stop at the end.
  useEffect(() => {
    if (!playing || !trace) return;
    let last = performance.now();
    let id = requestAnimationFrame(function step(now) {
      const next = Math.min(trace.t1, tRef.current + ((now - last) / 1000) * rate);
      last = now;
      setT(next);
      if (next >= trace.t1) setPlaying(false);
      else id = requestAnimationFrame(step);
    });
    return () => cancelAnimationFrame(id);
  }, [playing, rate, trace]);

  // The hash keeps the trace and, while paused, the time.
  useEffect(() => {
    if (!name || playing) return;
    history.replaceState(null, "", `#sim?trace=${encodeURIComponent(name)}&t=${t.toFixed(1)}`);
  }, [name, playing, t]);

  const markers = useMemo(() => (trace ? buildMarkers(trace, rows, only ? selected : null) : []), [trace, rows, only, selected]);
  const jump = useCallback(
    (dir: 1 | -1) => {
      const now = tRef.current;
      const m = dir > 0 ? markers.find((x) => x.t > now + 1e-6) : [...markers].reverse().find((x) => x.t < now - 1e-6);
      if (m) seek(m.t);
    },
    [markers, seek],
  );
  /** Play or pause; play at the end starts over. */
  const togglePlay = useCallback(() => {
    if (!trace) return;
    if (!playing && tRef.current >= trace.t1) seek(trace.t0);
    setPlaying(!playing);
  }, [trace, playing, seek]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e.target) || !trace) return;
      const step = e.ctrlKey ? 10 : e.shiftKey ? 1 : 0.1;
      if (e.key === " ") togglePlay();
      else if (e.key === "ArrowLeft") seek(tRef.current - step);
      else if (e.key === "ArrowRight") seek(tRef.current + step);
      else if (e.key === "[") jump(-1);
      else if (e.key === "]") jump(1);
      else if (e.key === "Escape") setSelected(null);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [trace, seek, jump, togglePlay]);

  const frame = useMemo(() => (trace ? trace.stateAt(t) : null), [trace, t]);
  const fx = useMemo(() => (trace && frame ? effectsAt(trace, frame, t) : null), [trace, frame, t]);
  const head = trace?.head;

  return (
    <div className="sim">
      <div className="sim-bar panel">
        <select value={name ?? ""} onChange={(e) => setName(e.target.value || null)} title={list ? `traces in ${list.dir}` : undefined}>
          {!list?.traces.length && <option value="">{list ? "no traces" : "loading…"}</option>}
          {name && list && !list.traces.some((x) => x.name === name) && <option value={name}>{name}</option>}
          {list?.traces.map((x) => (
            <option key={x.name} value={x.name}>
              {x.head ? `${x.head.label} s${x.head.seed} · ${x.head.created}` : x.name} · {fmtSize(x.size)}
            </option>
          ))}
        </select>
        <button type="button" onClick={refresh} title="re-read the trace directory">
          ↻
        </button>
        <button
          type="button"
          className={runOpen ? "active" : undefined}
          onClick={() => setRunOpen((o) => !o)}
          title="start run.py or a fight script; recent sim jobs and their traces"
        >
          run…
        </button>
        {sim.running && (
          <Badge kind="info" title={sim.running.argv.join(" ")}>
            running: {sim.running.label}
          </Badge>
        )}
        {listError && <span className="error">{listError}</span>}
        {list && !list.traces.length && (
          <span className="dim">
            No traces in {list.dir}: start one with run… (record traces), or run <code>python harness/sim/run.py --trace</code> or a fight script with{" "}
            <code>--trace DIR</code>.
          </span>
        )}
        <span className="dim">{status}</span>
        {head && (
          <span className="dim mono" title={head.runner_args.join(" ")}>
            {head.scenario} · seed {head.seed}
            {trace?.end ? ` · ${Object.entries(trace.end.summary).map(([k, v]) => `${k} ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ")}` : ""}
          </span>
        )}
      </div>
      <SimRunPanel open={runOpen} onClose={() => setRunOpen(false)} sim={sim} current={name} onLoad={loadJobTrace} />
      {trace && frame && fx ? (
        <>
          <main className="sim-map panel">
            <SimMap trace={trace} frame={frame} fx={fx} t={t} selected={selected} onSelect={setSelected} follow={follow} onFollow={setFollow} />
          </main>
          <aside className="sim-side">
            <Panel title={`Inspector${selected !== null ? ` · ${trace.nameOf(selected)}` : ""}`} className="sim-inspector">
              <SimInspector trace={trace} frame={frame} t={t} selected={selected} onSeek={seek} />
            </Panel>
            <Panel title="Runner" className="sim-runner">
              <SimRunnerPanel trace={trace} t={t} onSeek={seek} />
            </Panel>
          </aside>
          <section className="sim-bottom panel">
            <SimTimeline
              trace={trace}
              t={t}
              onSeek={seek}
              playing={playing}
              onPlay={togglePlay}
              rate={rate}
              onRate={setRate}
              markers={markers}
              rows={rows}
              onRows={setRows}
              only={only}
              onOnly={setOnly}
              onPrev={() => jump(-1)}
              onNext={() => jump(1)}
            />
            <SimDamageCharts trace={trace} t={t} selected={selected} onSeek={seek} />
          </section>
        </>
      ) : (
        <div className="sim-empty dim">{status || "Pick a trace."}</div>
      )}
    </div>
  );
}

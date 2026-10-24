// The Sim page's mobile inspector: the selected mobile's state at t (position, hits, war mode, threat,
// timers, cast, statuses), its last events (click to seek) and, for the player, the world's timers.
import { DIR_NAMES } from "../format.ts";
import { describeEvent, fmtSimT, involves } from "../sim/markers.ts";
import { statusTags } from "../sim/status.ts";
import type { Trace } from "../sim/trace.ts";
import type { Frame } from "../sim/types.ts";

const left = (until: number | null | undefined, t: number) => (until && until > t ? `${(until - t).toFixed(1)} s` : "—");

function Table({ trace, rows }: { trace: Trace; rows: Record<string, number> }) {
  const sorted = Object.entries(rows).sort((a, b) => b[1] - a[1]);
  if (!sorted.length) return <div className="dim">none</div>;
  return (
    <ul className="sim-list">
      {sorted.map(([s, n]) => (
        <li key={s}>
          <span className="t">{Math.round(n)}</span>
          {trace.nameOf(Number(s))}
        </li>
      ))}
    </ul>
  );
}

export function SimInspector({ trace, frame, t, selected, onSeek }: { trace: Trace; frame: Frame; t: number; selected: number | null; onSeek: (t: number) => void }) {
  if (selected === null) return <div className="dim">Click a mobile on the map to inspect it.</div>;
  const m = frame.mobs.get(selected);
  const player = trace.head?.player;
  const recent = trace.lastEvents(t, 12, (e) => involves(e, selected, player));
  const events = (
    <>
      <h4 className="sim-sub">last events</h4>
      <ul className="sim-list">
        {recent.map((e, i) => (
          <li key={i} onClick={() => onSeek(e.t)} title="seek here">
            <span className="t">{fmtSimT(e.t)}</span>
            {describeEvent(e, trace)}
          </li>
        ))}
        {!recent.length && <li className="dim">none yet</li>}
      </ul>
    </>
  );
  if (!m) {
    return (
      <div>
        <div>
          <b>{trace.nameOf(selected)}</b> <span className="dim">is gone at {fmtSimT(t)}</span>
        </div>
        {events}
      </div>
    );
  }
  const isPlayer = m.kind === "player";
  const w = frame.w;
  const cast = m.cs;
  const cds = Object.entries(m.cd ?? {}).filter(([, until]) => until > t);
  const statuses = statusTags(m, isPlayer ? w : null, t);
  const songs = Object.entries(w.songs ?? {}).filter(([, until]) => until > t);
  return (
    <div>
      <dl className="sim-kv">
        <dt>name</dt>
        <dd>
          {m.name} <span className="dim">#{m.s}</span>
        </dd>
        <dt>kind · key</dt>
        <dd>
          {m.kind} · {m.key ?? "—"}
          {m.cp ? ` · copy of ${trace.nameOf(m.cp)}` : ""}
        </dd>
        <dt>position</dt>
        <dd>
          {m.x}, {m.y}, {m.z} facing {DIR_NAMES[m.d & 7]}
        </dd>
        <dt>hits</dt>
        <dd>
          {m.h}/{m.hm}
          {m.gh ? " (ghost)" : !m.al ? " (dead)" : ""}
        </dd>
        {isPlayer && (
          <>
            <dt>mana · stam</dt>
            <dd>
              {m.mn ?? "?"}/{m.mm ?? "?"} · {m.st ?? "?"}/{m.sm ?? "?"}
            </dd>
          </>
        )}
        <dt>war · target</dt>
        <dd>
          {m.w ? "war" : "peace"} · {m.tg != null ? trace.nameOf(m.tg) : "—"}
        </dd>
        {m.md && (
          <>
            <dt>pet mode</dt>
            <dd>{m.md}</dd>
          </>
        )}
        <dt>next swing</dt>
        <dd>{m.ns > t ? `in ${(m.ns - t).toFixed(1)} s` : "ready"}</dd>
        {m.ex ? (
          <>
            <dt>expires</dt>
            <dd>{left(m.ex, t)}</dd>
          </>
        ) : null}
        {m.ch && (
          <>
            <dt>charge</dt>
            <dd>
              lands {left(m.ch[0], t)} on {trace.nameOf(m.ch[1])}
            </dd>
          </>
        )}
        {m.fd && (
          <>
            <dt>feeding</dt>
            <dd>
              {left(m.fd[0], t)} on {trace.nameOf(m.fd[1])}
            </dd>
          </>
        )}
      </dl>
      {cast && (
        <>
          <h4 className="sim-sub">
            casting at {trace.nameOf(cast[1])} · releases {left(cast[0], t)}
          </h4>
          <div className="sim-bar-meter">
            <div style={{ width: `${cast[0] > cast[2] ? Math.min(100, Math.max(0, ((t - cast[2]) / (cast[0] - cast[2])) * 100)) : 100}%` }} />
          </div>
        </>
      )}
      <h4 className="sim-sub">statuses</h4>
      {statuses.length ? (
        <ul className="sim-list">
          {statuses.map((s) => (
            <li key={s.label}>
              <span className="sim-swatch" style={{ background: s.color }} /> {s.label} {s.what}
              {s.label === "PV" && m.pv ? ` at ${trace.nameOf(m.pv[0])}` : ""} · {left(s.until, t)}
            </li>
          ))}
        </ul>
      ) : (
        <div className="dim">none</div>
      )}
      <h4 className="sim-sub">cooldowns</h4>
      {cds.length ? (
        <ul className="sim-list">
          {cds.map(([k, until]) => (
            <li key={k}>
              {k} · {left(until, t)}
            </li>
          ))}
        </ul>
      ) : (
        <div className="dim">none</div>
      )}
      <h4 className="sim-sub">threat</h4>
      <Table trace={trace} rows={m.th ?? {}} />
      <h4 className="sim-sub">damage taken by source</h4>
      <Table trace={trace} rows={m.db ?? {}} />
      {isPlayer && (
        <>
          <h4 className="sim-sub">world</h4>
          <dl className="sim-kv">
            <dt>songs</dt>
            <dd>{songs.length ? songs.map(([k, until]) => `${k} ${left(until, t)}`).join(" · ") : "—"}</dd>
            <dt>stationary penalty</dt>
            <dd>{w.pen ? `${w.pen} steps to clear` : "—"}</dd>
            <dt>potion ready</dt>
            <dd>{w.pot > t ? `in ${left(w.pot, t)}` : "ready"}</dd>
            <dt>recall</dt>
            <dd>{w.rc ? `lands ${left(w.rc, t)}` : "—"}</dd>
            <dt>vet</dt>
            <dd>{w.vet > t ? `ends ${left(w.vet, t)}` : "—"}</dd>
            <dt>last PvP hit</dt>
            <dd>{w.pvp ? `${(t - w.pvp).toFixed(1)} s ago` : "—"}</dd>
          </dl>
        </>
      )}
      {events}
    </div>
  );
}

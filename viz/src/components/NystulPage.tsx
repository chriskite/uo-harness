import { useEffect, useState } from "react";
import { fetchNystulModels } from "../api.ts";
import { fmtClock } from "../format.ts";
import { thinkingAfterSwitch, thinkingChoices, type NystulModels } from "../nystul.ts";
import { NystulChat, type NystulFeed } from "./NystulChat.tsx";

/** Today's conversations show the clock time, older ones the local date. */
function fmtUpdated(t: number): string {
  const d = new Date(t * 1000);
  if (d.toDateString() === new Date().toDateString()) return fmtClock(t);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** The model and thinking level of the questions asked from now on (saved by the server). */
function NystulSettings({ feed }: { feed: NystulFeed }) {
  const [models, setModels] = useState<NystulModels | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    fetchNystulModels().then(
      (m) => live && setModels(m),
      (e: unknown) => live && setLoadError(String(e)),
    );
    return () => {
      live = false;
    };
  }, []);
  if (!feed.list) return null;
  const { model, thinking } = feed.list;
  if (!models) return <span className="dim">{loadError ?? "loading models…"}</span>;
  const choices = thinkingChoices(models, model);
  const listed = models.models.some((m) => m.selector === model);
  return (
    <span className="ny-settings">
      <label>
        model{" "}
        <select
          value={model}
          onChange={(e) => void feed.setSettings({ model: e.target.value, thinking: thinkingAfterSwitch(thinking, thinkingChoices(models, e.target.value)) })}
          title="The model for the questions asked from now on"
        >
          {!listed && <option value={model}>{model} (name, not a listed model)</option>}
          {models.models.map((m) => (
            <option key={m.selector} value={m.selector} title={m.name}>
              {m.selector}
            </option>
          ))}
        </select>
      </label>
      <label>
        thinking{" "}
        <select
          value={thinking}
          onChange={(e) => void feed.setSettings({ thinking: e.target.value })}
          title="How hard the model thinks before answering (the levels this model supports)"
        >
          {!choices.includes(thinking) && <option value={thinking}>{thinking}</option>}
          {choices.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      </label>
    </span>
  );
}

/** The #nystul page: conversation list on the left, the active conversation beside it. */
export function NystulPage({ feed }: { feed: NystulFeed }) {
  const convs = feed.list?.conversations ?? [];
  return (
    <div className="nystul-page">
      <aside className="ny-list panel">
        <button type="button" className="ny-new" onClick={() => feed.select(null)} disabled={feed.activeId === null}>
          New conversation
        </button>
        {feed.list === null && <p className="dim pad">Opening the archives…</p>}
        {feed.list !== null && convs.length === 0 && <p className="dim pad">No conversations yet.</p>}
        <ul>
          {convs.map((c) => (
            <li key={c.id} className="ny-row">
              <button
                type="button"
                className={c.id === feed.activeId ? "ny-conv active" : "ny-conv"}
                onClick={() => feed.select(c.id)}
                title={`${c.messages} messages`}
              >
                <span className="ny-conv-title">{c.title}</span>
                <span className="mono dim ny-conv-time">
                  {c.running && <span className="dot dot-ok" title="Nystul is answering" />} {fmtUpdated(c.t_updated)}
                </span>
              </button>
              <button
                type="button"
                className="ny-del"
                disabled={c.running}
                title={c.running ? "Stop Nystul's answer before deleting" : "Delete this conversation"}
                aria-label={`Delete conversation: ${c.title}`}
                onClick={() => {
                  if (window.confirm(`Delete "${c.title}" and its ${c.messages} messages?`)) void feed.remove(c.id);
                }}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <main className="ny-main panel">
        <header className="ny-title">
          <h1>Nystul the Wizard</h1>
          <span className="dim">court mage · reads the Codex, the archives and the scrying pool</span>
          <NystulSettings feed={feed} />
        </header>
        <NystulChat feed={feed} />
      </main>
    </div>
  );
}

import { fmtClock } from "../format.ts";
import { NystulChat, type NystulFeed } from "./NystulChat.tsx";

/** Today's conversations show the clock time, older ones the local date. */
function fmtUpdated(t: number): string {
  const d = new Date(t * 1000);
  if (d.toDateString() === new Date().toDateString()) return fmtClock(t);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
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
            <li key={c.id}>
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
            </li>
          ))}
        </ul>
      </aside>
      <main className="ny-main panel">
        <header className="ny-title">
          <h1>Nystul the Wizard</h1>
          <span className="dim">
            court mage · reads the Codex, the archives and the scrying pool
            {feed.list && ` · ${feed.list.model}, thinking ${feed.list.thinking}`}
          </span>
        </header>
        <NystulChat feed={feed} />
      </main>
    </div>
  );
}

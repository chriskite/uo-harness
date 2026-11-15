import { useEffect, useState, type ReactNode } from "react";
import { fetchDiscordMessage, fetchKnowledgeEntry } from "../api.ts";
import {
  discordLink,
  discordSource,
  splitUrls,
  type DiscordMessage,
  type DiscordThread,
  type KnowledgeEntry,
  type NystulSource,
} from "../nystul.ts";
import { SourceLink } from "./Markdown.tsx";

type Open = (s: NystulSource) => void;

/** Local "YYYY-MM-DD HH:MM". */
function fmtWhen(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function CodexRef({ id, onOpen }: { id: number; onOpen: Open }) {
  return (
    <button type="button" className="ny-ref" onClick={() => onOpen({ kind: "knowledge", id })}>
      #{id}
    </button>
  );
}

/** Text with its URLs as links; Discord message links open in the drawer. */
function Linked({ text, onOpen }: { text: string; onOpen: Open }) {
  return (
    <>
      {splitUrls(text).map((p, i) => {
        if (typeof p === "string") return p;
        const src = discordSource(p.href);
        return src ? (
          <SourceLink key={i} href={p.href} source={src} onSource={onOpen}>
            {p.href}
          </SourceLink>
        ) : (
          <a key={i} href={p.href} target="_blank" rel="noreferrer">
            {p.href}
          </a>
        );
      })}
    </>
  );
}

function KnowledgeView({ e, onOpen }: { e: KnowledgeEntry; onOpen: Open }) {
  const rows: [label: string, value: ReactNode][] = [
    ["kind", e.kind],
    ["status", <span className={`ny-src-status ny-src-${e.status}`}>{e.status}</span>],
    ["source", e.source_type],
    ["reference", e.source_ref ? <Linked text={e.source_ref} onOpen={onOpen} /> : <span className="dim">none</span>],
    ["confidence", e.confidence.toFixed(2)],
    ["importance", String(e.importance)],
    ["tags", e.tags.length ? e.tags.join(" ") : <span className="dim">none</span>],
    ["entities", e.entities.length ? e.entities.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(", ") : <span className="dim">none</span>],
  ];
  if (e.x !== null && e.y !== null) rows.push(["location", `facet ${e.facet ?? 0} · ${e.x}, ${e.y}`]);
  rows.push(
    ["confirmations", String(e.confirmations)],
    ["recalled", e.access_count ? `${e.access_count}× · last ${e.last_access_t ? fmtWhen(new Date(e.last_access_t * 1000)) : "?"}` : "never"],
    ["created", fmtWhen(new Date(e.created_t * 1000))],
    ["updated", fmtWhen(new Date(e.updated_t * 1000))],
  );
  if (e.supersedes !== null) rows.push(["supersedes", <CodexRef id={e.supersedes} onOpen={onOpen} />]);
  if (e.superseded_by !== null) rows.push(["superseded by", <CodexRef id={e.superseded_by} onOpen={onOpen} />]);
  if (e.retract_reason) rows.push(["retracted because", e.retract_reason]);
  return (
    <>
      <div className="ny-src-kicker">Codex entry #{e.id}</div>
      <h2 className="ny-src-title">{e.topic}</h2>
      <p className="ny-src-content">{e.content}</p>
      <dl className="ny-src-fields">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {e.history.length > 0 && (
        <section className="ny-src-history">
          <h3>Earlier versions</h3>
          <ol>
            {e.history.map((h) => (
              <li key={h.id}>
                <div className="mono dim">
                  <CodexRef id={h.id} onOpen={onOpen} /> {fmtWhen(new Date(h.created_t * 1000))} · {h.status} · conf {h.confidence.toFixed(2)}
                </div>
                <div>{h.content}</div>
              </li>
            ))}
          </ol>
        </section>
      )}
    </>
  );
}

function DiscordRow({ m, cited, onOpen }: { m: DiscordMessage; cited?: boolean; onOpen: Open }) {
  const src: NystulSource = { kind: "discord", guild: m.guild_id ?? "@me", channel: m.channel_id, message: m.id };
  return (
    <li className={cited ? "ny-dc-msg cited" : "ny-dc-msg"}>
      <div className="ny-dc-meta">
        <span className="ny-dc-author">{m.author ?? m.author_id ?? "?"}</span>{" "}
        {cited ? (
          <span className="mono dim">{fmtWhen(new Date(m.ts))}</span>
        ) : (
          <button type="button" className="ny-ref" title="open this message here" onClick={() => onOpen(src)}>
            {fmtWhen(new Date(m.ts))}
          </button>
        )}
        {m.edited_ts && <span className="dim"> (edited)</span>}
        <span className="spacer" />
        <a href={m.link} target="_blank" rel="noreferrer" title="open in Discord">
          Discord ↗
        </a>
      </div>
      {m.content && <div className="ny-dc-text">{m.content}</div>}
      {m.embed_text && <div className="ny-dc-embed">{m.embed_text}</div>}
      {m.attachments.length > 0 && (
        <div className="dim ny-dc-files">attachments: {m.attachments.map((a) => a.filename ?? "file").join(", ")} (open in Discord to view)</div>
      )}
    </li>
  );
}

function DiscordView({ d, onOpen }: { d: DiscordThread; onOpen: Open }) {
  const { message: m, reply } = d;
  return (
    <>
      <div className="ny-src-kicker">Discord · #{d.channel ?? m.channel_id}</div>
      <h2 className="ny-src-title">
        {m.author ?? "?"} · {fmtWhen(new Date(m.ts))}
      </h2>
      <a className="ny-src-open" href={m.link} target="_blank" rel="noreferrer">
        Read the original in Discord ↗
      </a>
      {reply && (
        <div className="ny-dc-reply">
          <span className="dim">replying to</span>{" "}
          <button
            type="button"
            className="ny-ref"
            onClick={() => onOpen({ kind: "discord", guild: reply.guild_id ?? "@me", channel: reply.channel_id, message: reply.id })}
          >
            {reply.author ?? "?"}
          </button>
          : {reply.content.length > 200 ? `${reply.content.slice(0, 200)}…` : reply.content}
        </div>
      )}
      <ol className="ny-dc-thread">
        {d.before.map((x) => (
          <DiscordRow key={x.id} m={x} onOpen={onOpen} />
        ))}
        <DiscordRow m={m} cited onOpen={onOpen} />
        {d.after.map((x) => (
          <DiscordRow key={x.id} m={x} onOpen={onOpen} />
        ))}
      </ol>
    </>
  );
}

type Loaded = { kind: "knowledge"; entry: KnowledgeEntry } | { kind: "discord"; thread: DiscordThread };

/** The #nystul page's right-hand drawer: the source on top of `stack` (a Codex entry with every
 *  field and its earlier versions, or a Discord message with its neighbours and a link to the
 *  original). Sources opened from inside the drawer stack up; Back returns. */
export function NystulSourceDrawer({ stack, onOpen, onBack, onClose }: { stack: NystulSource[]; onOpen: Open; onBack: () => void; onClose: () => void }) {
  const src = stack[stack.length - 1];
  const key = src === undefined ? "" : src.kind === "knowledge" ? `k${src.id}` : `d${src.message}`;
  const [state, setState] = useState<{ key: string; data?: Loaded; error?: string }>({ key: "" });

  useEffect(() => {
    if (src === undefined) return;
    let live = true;
    const load: Promise<Loaded> =
      src.kind === "knowledge"
        ? fetchKnowledgeEntry(src.id).then((entry) => ({ kind: "knowledge", entry }))
        : fetchDiscordMessage(src.message).then((thread) => ({ kind: "discord", thread }));
    load.then(
      (data) => live && setState({ key, data }),
      (e: unknown) => live && setState({ key, error: e instanceof Error ? e.message : String(e) }),
    );
    return () => {
      live = false;
    };
    // the key names the source; src is a fresh object each render
  }, [key]);

  if (src === undefined) return null;
  const current = state.key === key ? state : { key };
  return (
    <aside className="ny-src panel" aria-label="cited source">
      <header className="ny-src-head">
        {stack.length > 1 && (
          <button type="button" onClick={onBack} title="back to the previous source">
            ← back
          </button>
        )}
        <span className="ny-src-head-title">{src.kind === "knowledge" ? "The Codex" : "The town criers"}</span>
        <span className="spacer" />
        <button type="button" className="ny-del" onClick={onClose} title="close (Esc)" aria-label="close the source drawer">
          ×
        </button>
      </header>
      <div className="ny-src-body">
        {current.error !== undefined ? (
          <>
            <p className="error">{current.error}</p>
            {src.kind === "discord" && (
              <a className="ny-src-open" href={discordLink(src)} target="_blank" rel="noreferrer">
                Read the original in Discord ↗
              </a>
            )}
          </>
        ) : current.data === undefined ? (
          <p className="dim">Consulting the archives…</p>
        ) : current.data.kind === "knowledge" ? (
          <KnowledgeView e={current.data.entry} onOpen={onOpen} />
        ) : (
          <DiscordView d={current.data.thread} onOpen={onOpen} />
        )}
      </div>
    </aside>
  );
}

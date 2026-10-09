import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { fetchNystulConversation, fetchNystulList, postNystulApprove, postNystulAsk, postNystulCancel } from "../api.ts";
import { fmtClock } from "../format.ts";
import {
  NYSTUL_MAX_CHARS,
  askError,
  isRunning,
  proposalView,
  runSummary,
  stepLabel,
  type NystulConversation,
  type NystulList,
  type NystulMessage,
  type NystulProposal,
} from "../nystul.ts";
import { Markdown } from "./Markdown.tsx";

const CONV_POLL_MS = 1_000;
const LIST_POLL_MS = 10_000;
const ACTIVE_KEY = "uo-viz-nystul-conv";

export interface NystulFeed {
  list: NystulList | null;
  /** The open conversation; null = a new one (the next ask starts it). */
  activeId: number | null;
  conv: NystulConversation | null;
  error: string | null;
  sending: boolean;
  select: (id: number | null) => void;
  /** Resolves true once the question is accepted. */
  ask: (text: string) => Promise<boolean>;
  cancel: () => void;
  /** Apply a Codex change Nystul proposed; resolves true once the server applied it. */
  approve: (proposal: number) => Promise<boolean>;
}

function storedActive(): number | null {
  const v = Number(localStorage.getItem(ACTIVE_KEY));
  return Number.isInteger(v) && v > 0 ? v : null;
}

/** Nystul's conversation list and the active conversation, shared by the #nystul page
 *  and the Live-page panel. The active conversation is polled every second while an
 *  answer runs; the list on mount, after each ask, when a run ends and every 10 s. */
export function useNystul(): NystulFeed {
  const [list, setList] = useState<NystulList | null>(null);
  const [activeId, setActiveId] = useState<number | null>(storedActive);
  const [conv, setConv] = useState<NystulConversation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const active = useRef(activeId);
  const listBusy = useRef(false);
  const convBusy = useRef(false);
  /** Only the newest conversation request may apply (a select or ask overtakes a poll). */
  const convSeq = useRef(0);

  const refreshList = useCallback(() => {
    if (listBusy.current) return;
    listBusy.current = true;
    fetchNystulList()
      .then(
        (l) => {
          setList(l);
          setError(null);
        },
        (e: unknown) => setError(String(e)),
      )
      .finally(() => {
        listBusy.current = false;
      });
  }, []);

  const choose = useCallback((id: number | null) => {
    active.current = id;
    if (id === null) localStorage.removeItem(ACTIVE_KEY);
    else localStorage.setItem(ACTIVE_KEY, String(id));
    setActiveId(id);
  }, []);

  /** Fetch the active conversation; `poll` ticks skip while one is in flight. */
  const loadConv = useCallback(
    (poll: boolean) => {
      const id = active.current;
      if (id === null || (poll && convBusy.current)) return;
      const seq = ++convSeq.current;
      convBusy.current = true;
      fetchNystulConversation(id)
        .then(
          (c) => {
            if (seq !== convSeq.current || active.current !== id) return;
            setError(null);
            if (c) setConv(c);
            else {
              // The server no longer has it (a fresh nystul.db): start over.
              setConv(null);
              choose(null);
            }
          },
          (e: unknown) => {
            if (seq === convSeq.current) setError(String(e));
          },
        )
        .finally(() => {
          if (seq === convSeq.current) convBusy.current = false;
        });
    },
    [choose],
  );

  useEffect(() => {
    refreshList();
    const t = setInterval(refreshList, LIST_POLL_MS);
    return () => clearInterval(t);
  }, [refreshList]);

  // Load once on select (and on mount).
  useEffect(() => {
    setConv(null);
    loadConv(false);
  }, [activeId, loadConv]);

  const running = isRunning(conv);
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) refreshList();
    wasRunning.current = running;
    if (!running) return;
    const t = setInterval(() => loadConv(true), CONV_POLL_MS);
    return () => clearInterval(t);
  }, [running, loadConv, refreshList]);

  const select = useCallback(
    (id: number | null) => {
      if (id !== active.current) choose(id);
    },
    [choose],
  );

  const ask = useCallback(
    async (text: string) => {
      setSending(true);
      try {
        const r = await postNystulAsk(active.current, text.trim());
        setError(null);
        if (r.conversation !== active.current) choose(r.conversation);
        else loadConv(false);
        refreshList();
        return true;
      } catch (e) {
        setError(String(e));
        return false;
      } finally {
        setSending(false);
      }
    },
    [choose, loadConv, refreshList],
  );

  const cancel = useCallback(() => {
    const id = active.current;
    if (id === null) return;
    postNystulCancel(id).then(
      () => loadConv(false),
      (e: unknown) => setError(String(e)),
    );
  }, [loadConv]);

  const approve = useCallback(
    async (proposal: number) => {
      try {
        await postNystulApprove(proposal);
        setError(null);
        return true;
      } catch (e) {
        setError(String(e));
        return false;
      } finally {
        loadConv(false);
      }
    },
    [loadConv],
  );

  return { list, activeId, conv, error, sending, select, ask, cancel, approve };
}

function ProposalCard({ p, onApprove }: { p: NystulProposal; onApprove: (id: number) => Promise<boolean> }) {
  const v = proposalView(p);
  const [busy, setBusy] = useState(false);
  return (
    <div className={`ny-proposal ny-proposal-${p.status}`}>
      <div className="ny-proposal-head">
        <span className="ny-proposal-title">Codex change: {v.title}</span>
      </div>
      {v.why && <div className="ny-proposal-why">{v.why}</div>}
      {v.before && (
        <div className="ny-proposal-before" title="the entry as it was when Nystul proposed this">
          <span className="dim">now #{v.before.id}:</span> [{v.before.topic}] {v.before.content}
        </div>
      )}
      {v.fields.length > 0 && (
        <dl className="ny-proposal-fields">
          {v.fields.map((f) => (
            <div key={f.label}>
              <dt>{f.label}</dt>
              <dd>{f.value}</dd>
            </div>
          ))}
        </dl>
      )}
      <div className="ny-proposal-actions">
        {v.outcome !== null ? (
          <span className={p.status === "failed" ? "error" : "dim"}>{v.outcome}</span>
        ) : (
          <button
            type="button"
            className="ny-approve"
            disabled={busy}
            title="write this change into the Codex (the memory store the Seer recalls)"
            onClick={() => {
              setBusy(true);
              void onApprove(p.id).finally(() => setBusy(false));
            }}
          >
            {busy ? "approving…" : "Approve"}
          </button>
        )}
      </div>
    </div>
  );
}

function WizardMessage({ msg, onApprove }: { msg: NystulMessage; onApprove: (id: number) => Promise<boolean> }) {
  const running = msg.status === "running";
  return (
    <div className="ny-msg ny-wizard">
      <div className="ov-meta">
        <span className="ov-role">Nystul</span> <span className="mono dim ov-time">{fmtClock(msg.t)}</span>
        {msg.status === "cancelled" && <span className="dim">stopped</span>}
      </div>
      {(msg.steps.length > 0 || running) && (
        <details className="ny-steps" open={running}>
          <summary>
            {msg.steps.length} {msg.steps.length === 1 ? "lookup" : "lookups"}
          </summary>
          <ol>
            {msg.steps.map((s, i) => (
              <li key={i} title={JSON.stringify(s.args)} className={s.ok === false ? "bad" : undefined}>
                <span className="mono ny-mark">{s.ok === null ? "…" : s.ok ? "✓" : "✗"}</span> {stepLabel(s)}
              </li>
            ))}
          </ol>
        </details>
      )}
      {running && msg.text === "" ? <p className="dim ny-ponder">Nystul ponders…</p> : <Markdown text={msg.text} />}
      {msg.proposals.map((p) => (
        <ProposalCard key={p.id} p={p} onApprove={onApprove} />
      ))}
      {!running && <div className="mono dim ny-summary">{runSummary(msg)}</div>}
      {msg.status === "error" && msg.error && <div className="error">{msg.error}</div>}
    </div>
  );
}

function UserMessage({ msg }: { msg: NystulMessage }) {
  return (
    <div className="ny-msg ny-user">
      <div className="ov-meta">
        <span className="ov-role">you</span> <span className="mono dim ov-time">{fmtClock(msg.t)}</span>
      </div>
      <div className="ov-text">{msg.text}</div>
    </div>
  );
}

/** The active conversation with Nystul plus the compose box. */
export function NystulChat({ feed, compact }: { feed: NystulFeed; compact?: boolean }) {
  const { list, conv, error, sending } = feed;
  const [text, setText] = useState("");
  const running = isRunning(conv);
  const available = list?.available ?? true;
  const invalid = askError(text);
  const messages = conv?.messages ?? [];
  const last = messages[messages.length - 1];
  const body = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  useLayoutEffect(() => {
    const el = body.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [messages.length, last?.text.length, last?.steps.length, last?.proposals?.length]);

  const send = () => {
    if (invalid || sending || running || !available) return;
    pinned.current = true;
    void feed.ask(text).then((ok) => {
      if (ok) setText("");
    });
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className={compact ? "nystul ny-compact" : "nystul"}>
      <div
        className="ny-body"
        ref={body}
        onScroll={(e) => {
          const el = e.currentTarget;
          pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
      >
        {messages.length === 0 && (
          <p className="dim pad">
            Ask Nystul about the character, the jobs, the Codex or the harness. He only looks things up, never acts in game, and
            changes the Codex only when you approve his proposal.
          </p>
        )}
        {messages.map((m) =>
          m.role === "user" ? <UserMessage key={m.id} msg={m} /> : <WizardMessage key={m.id} msg={m} onApprove={feed.approve} />,
        )}
      </div>
      <div className="ov-compose">
        {!available && <div className="ov-hint">Nystul cannot be summoned: omp is not on this computer's PATH.</div>}
        <textarea
          value={text}
          placeholder="Ask Nystul… (Enter sends, Shift+Enter new line)"
          rows={2}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
        />
        <div className="ov-send">
          <span className={text.trim().length > NYSTUL_MAX_CHARS ? "mono bad" : "mono dim"}>
            {text.trim().length}/{NYSTUL_MAX_CHARS}
          </span>
          {error && <span className="error">{error}</span>}
          <span className="spacer" />
          {running ? (
            <button type="button" onClick={feed.cancel} title="stop Nystul's answer">
              stop
            </button>
          ) : (
            <button type="button" disabled={invalid !== null || sending || !available} onClick={send} title={invalid ?? "send"}>
              {sending ? "sending…" : "send"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { fetchOverseer, postChat } from "../api.ts";
import { fmtClock } from "../format.ts";
import {
  CHAT_MAX_CHARS,
  EMPTY_OVERSEER,
  chatError,
  mergeOverseer,
  overseerStatus,
  thoughtPreview,
  timeline,
  type ChatRow,
  type Juncture,
  type OverseerState,
} from "../overseer.ts";

const POLL_MS = 2_000;

export interface OverseerFeed {
  state: OverseerState;
  error: string | null;
  /** Poll now (after a send). */
  refresh: () => void;
}

/** Poll /api/overseer every 2 s, merging rows above the cursors. */
export function useOverseer(): OverseerFeed {
  const [state, setState] = useState<OverseerState>(EMPTY_OVERSEER);
  const [error, setError] = useState<string | null>(null);
  const cursor = useRef({ chat: 0, juncture: 0 });
  const busy = useRef(false);

  const refresh = useCallback(() => {
    if (busy.current) return;
    busy.current = true;
    fetchOverseer(cursor.current.chat, cursor.current.juncture)
      .then(
        (resp) => {
          setError(null);
          setState((s) => {
            const next = mergeOverseer(s, resp);
            cursor.current = { chat: next.afterChat, juncture: next.afterJuncture };
            return next;
          });
        },
        (e: unknown) => setError(String(e)),
      )
      .finally(() => {
        busy.current = false;
      });
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  return { state, error, refresh };
}

const SEVERITY_LABEL: Record<string, string> = { info: "info", attention: "attention", urgent: "URGENT" };

function ChatItem({ row }: { row: ChatRow }) {
  const time = <span className="mono dim ov-time">{fmtClock(row.t)}</span>;
  if (row.kind === "thought") {
    return (
      <details className="ov-item ov-thought">
        <summary>
          {time} <span className="ov-thinking">thinking</span> <span className="ov-preview">{thoughtPreview(row.text)}</span>
        </summary>
        <div className="ov-text">{row.text}</div>
      </details>
    );
  }
  if (row.kind === "action") {
    return (
      <div className="ov-item ov-action">
        {time} <span className="ov-tag">action</span> <span className="ov-text mono">{row.text}</span>
      </div>
    );
  }
  return (
    <div className={`ov-item ov-msg ov-${row.role}`}>
      <div className="ov-meta">
        <span className="ov-role">{row.role === "user" ? "you" : row.role}</span> {time}
      </div>
      <div className="ov-text">{row.text}</div>
    </div>
  );
}

function JunctureItem({ row, open }: { row: Juncture; open: boolean }) {
  return (
    <div className={`ov-item ov-juncture sev-${row.severity}${open ? "" : " acked"}`}>
      <div className="ov-meta">
        <span className="ov-sev">{SEVERITY_LABEL[row.severity] ?? row.severity}</span>
        <span className="mono">
          {row.source}/{row.kind}
        </span>
        <span className="mono dim ov-time">{fmtClock(row.t)}</span>
        {!open && <span className="dim">acked</span>}
      </div>
      <div className="ov-text">{row.summary}</div>
    </div>
  );
}

/** Chat with the overseer AI plus its thoughts, actions and open junctures in one timeline. */
export function OverseerPanel({ feed }: { feed: OverseerFeed }) {
  const { state, error, refresh } = feed;
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [showAcked, setShowAcked] = useState(false);
  const items = useMemo(() => timeline(state, showAcked), [state, showAcked]);
  const status = overseerStatus(state.heartbeat, state.now);
  const invalid = chatError(text);
  const body = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  useLayoutEffect(() => {
    const el = body.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [items.length]);

  const send = () => {
    if (invalid || sending) return;
    setSending(true);
    postChat(text.trim()).then(
      () => {
        setText("");
        setSendError(null);
        setSending(false);
        pinned.current = true;
        refresh();
      },
      (e: unknown) => {
        setSendError(String(e));
        setSending(false);
      },
    );
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className="overseer">
      <header className="panel-head">
        <h2>
          Overseer{" "}
          {state.openIds.length > 0 && <span className="ov-open">{state.openIds.length} open</span>}
        </h2>
        <span className={status.active ? "conn ov-status active" : "conn ov-status"} title="from the overseer's heartbeat (meta overseer_heartbeat)">
          <span className={status.active ? "dot dot-ok" : "dot dot-idle"} /> {status.text}
        </span>
      </header>
      <div className="log-filters">
        <label title="also show junctures the overseer has acknowledged">
          <input type="checkbox" checked={showAcked} onChange={(e) => setShowAcked(e.target.checked)} /> acked junctures
        </label>
        <span className="dim">{state.chat.length} messages</span>
        {!state.store && <span className="warn">no memory store yet</span>}
        {error && <span className="error">{error}</span>}
      </div>
      <div
        className="ov-body"
        ref={body}
        onScroll={(e) => {
          const el = e.currentTarget;
          pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
      >
        {items.length === 0 && <p className="dim pad">No messages yet. Say something to the overseer below.</p>}
        {items.map((it) =>
          it.type === "chat" ? <ChatItem key={it.key} row={it.row} /> : <JunctureItem key={it.key} row={it.row} open={it.open} />,
        )}
      </div>
      <div className="ov-compose">
        {!status.active && (
          <div className="ov-hint">
            The overseer replies only while an overseer session is running (docs/OVERSEER.md). Messages wait in the memory store until then.
          </div>
        )}
        <textarea
          value={text}
          placeholder="Message the overseer… (Enter sends, Shift+Enter new line)"
          rows={2}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
        />
        <div className="ov-send">
          <span className={text.trim().length > CHAT_MAX_CHARS ? "mono bad" : "mono dim"}>
            {text.trim().length}/{CHAT_MAX_CHARS}
          </span>
          {sendError && <span className="error">{sendError}</span>}
          <span className="spacer" />
          <button type="button" disabled={invalid !== null || sending} onClick={send} title={invalid ?? "send"}>
            {sending ? "sending…" : "send"}
          </button>
        </div>
      </div>
    </div>
  );
}

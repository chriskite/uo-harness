// viz_server client (docs/VISUALIZER.md §2.1): initial REST fetch, SSE stream
// with resume (event batches + newest state, coalesced per task, sse.ts),
// periodic walk-memory refresh, playback and agent-gate control, job analytics
// and the overseer chat (§2.4), and the Nystul assistant chat.
import type { HuntResponse, JobsResponse, PlanResponse, RangeBounds } from "./jobs.ts";
import type { NystulConversation, NystulList } from "./nystul.ts";
import type { OverseerResponse } from "./overseer.ts";
import { supersede, type SseMessage } from "./sse.ts";
import type { VizStore } from "./store.ts";
import type { EventEnvelope, Gate, GateAction, GateResponse, PlaybackAction, StateResponse, WalkMemoryFile } from "./types.ts";

const WALKMEM_REFRESH_MS = 10_000;
const RECONNECT_MS = 2_000;

export async function fetchState(): Promise<StateResponse> {
  const r = await fetch("/api/state", { cache: "no-store" });
  if (!r.ok) throw new Error(`/api/state: HTTP ${r.status}`);
  return (await r.json()) as StateResponse;
}

/** Raw file text too, so an unchanged file is not re-parsed into a new object. */
async function fetchWalkmem(): Promise<{ text: string; data: WalkMemoryFile } | null> {
  const r = await fetch("/api/walkmem", { cache: "no-store" });
  if (!r.ok) return null;
  const text = await r.text();
  return { text, data: JSON.parse(text) as WalkMemoryFile };
}

export async function postPlayback(action: PlaybackAction): Promise<void> {
  const r = await fetch("/api/playback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(action),
  });
  if (!r.ok) throw new Error(`/api/playback: HTTP ${r.status} ${await r.text()}`);
}

/** Job analytics (harness/jobs.py) over [since, until) epoch s (null = unbounded);
 *  `tz` = minutes east of UTC for the per-day split. */
export function fetchJobs(job: "lumber", tz: number, range: RangeBounds): Promise<JobsResponse>;
export function fetchJobs(job: "hunt", tz: number, range: RangeBounds): Promise<HuntResponse>;
export async function fetchJobs(job: string, tz: number, range: RangeBounds): Promise<JobsResponse | HuntResponse> {
  const q = new URLSearchParams({ job, tz: String(tz) });
  if (range.since !== null) q.set("since", String(range.since));
  if (range.until !== null) q.set("until", String(range.until));
  const r = await fetch(`/api/jobs?${q}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`/api/jobs: HTTP ${r.status} ${await r.text()}`);
  return (await r.json()) as JobsResponse | HuntResponse;
}

/** The lumber optimizer's plan (seconds to compute when the server's per-minute cache is cold). */
export async function fetchLumberPlan(): Promise<PlanResponse> {
  const r = await fetch("/api/jobs/plan", { cache: "no-store" });
  if (!r.ok) throw new Error(`/api/jobs/plan: HTTP ${r.status} ${await r.text()}`);
  return (await r.json()) as PlanResponse;
}

/** Chat rows and junctures above the cursors (0 = the newest 200), open junctures, heartbeat. */
export async function fetchOverseer(afterChat: number, afterJuncture: number): Promise<OverseerResponse> {
  const r = await fetch(`/api/overseer?after_chat=${afterChat}&after_juncture=${afterJuncture}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`/api/overseer: HTTP ${r.status} ${await r.text()}`);
  return (await r.json()) as OverseerResponse;
}

/** A user chat message for the overseer; resolves to its row id. */
export async function postChat(text: string): Promise<number> {
  const r = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  const body = (await r.json().catch(() => null)) as { ok?: boolean; id?: number; error?: string } | null;
  if (!r.ok || !body?.ok || typeof body.id !== "number") throw new Error(`chat: ${body?.error ?? `HTTP ${r.status}`}`);
  return body.id;
}

/** GET a Nystul route; null on 404, throws `nystul: <server error>` otherwise. */
async function getNystul(path: string): Promise<unknown> {
  const r = await fetch(path, { cache: "no-store" });
  if (r.status === 404) return null;
  const body: unknown = await r.json().catch(() => null);
  if (!r.ok || !body) {
    const err = body && typeof body === "object" && "error" in body ? String(body.error) : `HTTP ${r.status}`;
    throw new Error(`nystul: ${err}`);
  }
  return body;
}

/** Nystul's conversations (newest first) and whether omp is available. */
export async function fetchNystulList(): Promise<NystulList> {
  const body = await getNystul("/api/nystul");
  if (!body) throw new Error("nystul: HTTP 404");
  return body as NystulList;
}

/** One conversation with its messages; null when the server no longer has it (404). */
export async function fetchNystulConversation(id: number): Promise<NystulConversation | null> {
  return (await getNystul(`/api/nystul/${id}`)) as NystulConversation | null;
}

/** Ask Nystul (conversation null = start a new one); resolves to the conversation and Nystul's message id. */
export async function postNystulAsk(conversation: number | null, text: string): Promise<{ conversation: number; message: number }> {
  const r = await fetch("/api/nystul/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation, text }),
  });
  const body = (await r.json().catch(() => null)) as { ok?: boolean; conversation?: number; message?: number; error?: string } | null;
  if (!r.ok || !body?.ok || typeof body.conversation !== "number" || typeof body.message !== "number") {
    throw new Error(`nystul: ${body?.error ?? `HTTP ${r.status}`}`);
  }
  return { conversation: body.conversation, message: body.message };
}

/** Stop the running answer in `conversation`. */
export async function postNystulCancel(conversation: number): Promise<void> {
  const r = await fetch("/api/nystul/cancel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation }),
  });
  const body = (await r.json().catch(() => null)) as { ok?: boolean; error?: string } | null;
  if (!r.ok || !body?.ok) throw new Error(`nystul: ${body?.error ?? `HTTP ${r.status}`}`);
}

export type CaptchaMode = "human" | "auto";

/** `body.mode` when it is a captcha mode, else null. */
function captchaModeOf(body: unknown): CaptchaMode | null {
  if (!body || typeof body !== "object" || !("mode" in body)) return null;
  return body.mode === "human" || body.mode === "auto" ? body.mode : null;
}

/** Who answers the harvest captcha (memory store meta; "human" when unset). */
export async function fetchCaptchaMode(): Promise<CaptchaMode> {
  const r = await fetch("/api/captcha", { cache: "no-store" });
  if (!r.ok) throw new Error(`/api/captcha: HTTP ${r.status} ${await r.text()}`);
  const mode = captchaModeOf(await r.json());
  if (!mode) throw new Error("/api/captcha: no mode in the response");
  return mode;
}

export async function postCaptchaMode(mode: CaptchaMode): Promise<CaptchaMode> {
  const r = await fetch("/api/captcha", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  const body: unknown = await r.json().catch(() => null);
  const got = r.ok ? captchaModeOf(body) : null;
  if (!got) {
    const err = body && typeof body === "object" && "error" in body ? String(body.error) : `HTTP ${r.status}`;
    throw new Error(`captcha mode: ${err}`);
  }
  return got;
}

/** Pause/resume/kill the agent gate (live only). Resolves to the proxy's new gate;
 * throws with the proxy's (or viz_server's) error text otherwise. */
export async function postGate(action: GateAction): Promise<Gate | undefined> {
  const r = await fetch("/api/gate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  const text = await r.text();
  let resp: GateResponse | null = null;
  try {
    resp = JSON.parse(text) as GateResponse;
  } catch {
    // not JSON: fall through with the raw text
  }
  if (!r.ok || !resp?.ok) throw new Error(`gate ${action}: ${resp?.error ?? `HTTP ${r.status} ${text}`}`);
  return resp.gate;
}

/** Start feeding `store`; returns a stop function. */
export function connect(store: VizStore): () => void {
  let stopped = false;
  let source: EventSource | null = null;
  let reconnect: Timer | undefined;
  let walkText = "";

  const loadState = async () => {
    try {
      const resp = await fetchState();
      if (!stopped) store.setState(resp);
    } catch (e) {
      console.warn(e);
    }
  };

  const applyState = (resp: StateResponse) => {
    // A reset means the server restarted its seq numbering; the SSE resume
    // already skipped the new ring as "seen", so pull it via REST.
    if (store.setState(resp)) void loadState();
  };

  // SSE messages wait for the end of the dispatching task, then apply in order
  // with only the newest state parsed (sse.ts).
  let inbox: SseMessage[] = [];
  const drainInbox = () => {
    const msgs = supersede(inbox);
    inbox = [];
    if (stopped) return;
    for (const m of msgs) {
      if (m.kind === "state") applyState(JSON.parse(m.data) as StateResponse);
      else store.ingest(JSON.parse(m.data) as EventEnvelope[]);
    }
  };
  const receive = (kind: SseMessage["kind"]) => (m: Event) => {
    if (inbox.length === 0) setTimeout(drainInbox, 0);
    inbox.push({ kind, data: (m as MessageEvent<string>).data });
  };

  const open = () => {
    if (stopped) return;
    const since = store.getSnapshot().lastSeq + 1;
    const es = new EventSource(since > 0 ? `/api/events?since=${since}` : "/api/events");
    source = es;
    es.onopen = () => store.setConnected(true);
    es.addEventListener("state", receive("state"));
    es.addEventListener("world_events", receive("events"));
    es.onerror = () => {
      store.setConnected(false);
      // CONNECTING: the browser retries itself, resuming via Last-Event-ID.
      // CLOSED: it gave up; reopen with ?since= (a new EventSource has no Last-Event-ID).
      if (es.readyState === EventSource.CLOSED) {
        clearTimeout(reconnect);
        reconnect = setTimeout(open, RECONNECT_MS);
      }
    };
  };

  const refreshWalkmem = async () => {
    try {
      const w = await fetchWalkmem();
      if (w && w.text !== walkText && !stopped) {
        walkText = w.text;
        store.setWalkmem(w.data);
      }
    } catch (e) {
      console.warn(e);
    }
  };

  void loadState().then(open);
  void refreshWalkmem();
  const walkTimer = setInterval(refreshWalkmem, WALKMEM_REFRESH_MS);

  return () => {
    stopped = true;
    clearTimeout(reconnect);
    clearInterval(walkTimer);
    source?.close();
  };
}

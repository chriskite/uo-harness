// SSE message coalescing (docs/VISUALIZER.md §2.1). The browser dispatches all
// messages of one network chunk back to back in one task; api.ts queues them and
// applies them after that task, so a burst (a reader catching up) costs one
// JSON.parse of the newest state instead of one per state frame.

export interface SseMessage {
  kind: "state" | "events";
  data: string;
}

/** The messages to apply, in arrival order: every events batch, and of the
 * states only the last (it supersedes the earlier ones; the events batches
 * around it keep their places, so seqs stay ordered against the state's `next`). */
export function supersede(msgs: readonly SseMessage[]): SseMessage[] {
  let last = -1;
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i]!.kind === "state") {
      last = i;
      break;
    }
  }
  return msgs.filter((m, i) => m.kind !== "state" || i === last);
}

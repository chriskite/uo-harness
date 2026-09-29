// Last-write-wins store behind useSyncExternalStore: latest state, a 500-entry
// event ring (deduplicated by seq), connection flag, walk memory, selection,
// and running aggregates folded from every event seen.
import { useSyncExternalStore } from "react";
import { EVENT_RING, cloneAggregates, emptyAggregates, foldEvent, type Aggregates } from "./events.ts";
import type { EventEnvelope, HexSerial, StateResponse, WalkMemoryFile } from "./types.ts";

export interface VizSnapshot {
  /** Latest state-port response, without `events`. */
  state: StateResponse | null;
  /** Oldest first, at most EVENT_RING. */
  events: EventEnvelope[];
  /** Highest seq ingested, -1 before any. */
  lastSeq: number;
  connected: boolean;
  walkmem: WalkMemoryFile | null;
  agg: Aggregates;
  selected: HexSerial | null;
}

type Schedule = (flush: () => void) => void;

/** Coalesce notifications to one per frame (SSE bursts arrive as separate tasks). */
const frameSchedule: Schedule = (flush) => {
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(flush);
  else setTimeout(flush, 16);
};

export class VizStore {
  private snap: VizSnapshot;
  private agg: Aggregates = emptyAggregates();
  private ring: EventEnvelope[] = [];
  private eventsDirty = false;
  private pending = false;
  private readonly listeners = new Set<() => void>();

  constructor(private readonly schedule: Schedule = frameSchedule) {
    this.snap = {
      state: null,
      events: [],
      lastSeq: -1,
      connected: false,
      walkmem: null,
      agg: cloneAggregates(this.agg),
      selected: null,
    };
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): VizSnapshot => this.snap;

  /** Apply a state response. Returns true when it revealed a server-side reset
   * (seq numbering went backwards): the event ring and aggregates were cleared
   * and the caller should refetch /api/state to recover the new ring. */
  setState(resp: StateResponse): boolean {
    const { events, ...rest } = resp;
    const prev = this.snap.state;
    const sessionChanged = prev?.viz && rest.viz && prev.viz.session !== rest.viz.session;
    const reset = rest.next < this.snap.lastSeq + 1 || Boolean(sessionChanged);
    if (reset) this.clearEvents();
    this.patch({ state: rest });
    if (events) this.ingest(events);
    return reset;
  }

  /** Append envelopes with seq above the last seen (duplicates from resume overlap are dropped). */
  ingest(envs: readonly EventEnvelope[]): void {
    let last = this.snap.lastSeq;
    for (const env of envs) {
      if (env.seq <= last) continue;
      last = env.seq;
      this.ring.push(env);
      foldEvent(this.agg, env);
      this.eventsDirty = true;
    }
    if (!this.eventsDirty) return;
    if (this.ring.length > EVENT_RING * 2) this.ring = this.ring.slice(-EVENT_RING);
    this.patch({ lastSeq: last });
  }

  setConnected(connected: boolean): void {
    if (connected !== this.snap.connected) this.patch({ connected });
  }

  setWalkmem(walkmem: WalkMemoryFile | null): void {
    this.patch({ walkmem });
  }

  select = (selected: HexSerial | null): void => {
    this.patch({ selected });
  };

  private clearEvents(): void {
    this.ring = [];
    this.agg = emptyAggregates();
    this.eventsDirty = true;
    this.patch({ lastSeq: -1 });
  }

  private patch(p: Partial<VizSnapshot>): void {
    this.snap = { ...this.snap, ...p };
    if (this.pending) return;
    this.pending = true;
    this.schedule(() => this.flush());
  }

  private flush(): void {
    this.pending = false;
    if (this.eventsDirty) {
      this.eventsDirty = false;
      if (this.ring.length > EVENT_RING) this.ring = this.ring.slice(-EVENT_RING);
      this.snap = { ...this.snap, events: [...this.ring], agg: cloneAggregates(this.agg) };
    }
    for (const l of this.listeners) l();
  }
}

export const vizStore = new VizStore();

export function useViz(): VizSnapshot {
  return useSyncExternalStore(vizStore.subscribe, vizStore.getSnapshot);
}

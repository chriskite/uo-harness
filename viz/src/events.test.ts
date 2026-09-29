import { describe, expect, test } from "bun:test";
import {
  DEFAULT_FILTER,
  TRAIL_LEN,
  emptyAggregates,
  entityCaption,
  evVocabulary,
  filterEvents,
  foldEvent,
  labelOf,
  speechLabel,
  trafficSummary,
  type Aggregates,
} from "./events.ts";
import { cloneFixture, fixture, fixtureEvents } from "./fixtures/fixture.ts";
import { VizStore } from "./store.ts";
import type { EventEnvelope } from "./types.ts";

function foldAll(events: readonly EventEnvelope[]): Aggregates {
  const agg = emptyAggregates();
  for (const e of events) foldEvent(agg, e);
  return agg;
}

const env = (seq: number, origin: "world" | "proxy", data: EventEnvelope["data"], t = 100 + seq): EventEnvelope => ({
  seq,
  t,
  origin,
  data,
});

describe("EventLog filter", () => {
  test("keepalive is hidden by default and shown once un-hidden", () => {
    const keepalives = fixtureEvents.filter((e) => e.data.ev === "keepalive").length;
    expect(keepalives).toBeGreaterThan(0);
    const shown = filterEvents(fixtureEvents, DEFAULT_FILTER);
    expect(shown.some((e) => e.data.ev === "keepalive")).toBe(false);
    expect(shown.length).toBe(fixtureEvents.length - keepalives);

    const all = filterEvents(fixtureEvents, { ...DEFAULT_FILTER, hidden: new Set() });
    expect(all.length).toBe(fixtureEvents.length);
  });

  test("origin, serial and text filters combine", () => {
    const proxy = filterEvents(fixtureEvents, { ...DEFAULT_FILTER, origin: "proxy" });
    expect(proxy.every((e) => e.origin === "proxy")).toBe(true);
    expect(proxy.filter((e) => e.data.ev === "step").length).toBe(36);

    // Len (int 490 in events) matched through the hex serial filter.
    const len = filterEvents(fixtureEvents, { ...DEFAULT_FILTER, serial: "0x000001EA" });
    expect(len.length).toBeGreaterThan(0);
    expect(len.some((e) => e.data.ev === "speech_heard" && e.data.text === "Len the banker")).toBe(true);

    const text = filterEvents(fixtureEvents, { ...DEFAULT_FILTER, text: "BANKER" });
    expect(text.every((e) => JSON.stringify(e.data).toLowerCase().includes("banker"))).toBe(true);
    expect(text.length).toBeGreaterThan(0);
  });

  test("serial filter also matches `names` entries", () => {
    const events = [env(1, "world", { ev: "names", count: 1, entries: [{ serial: 490, name: "Len" }] })];
    expect(filterEvents(events, { ...DEFAULT_FILTER, serial: "0x000001EA" }).length).toBe(1);
    expect(filterEvents(events, { ...DEFAULT_FILTER, serial: "0x000001EB" }).length).toBe(0);
  });

  test("vocabulary lists every observed ev with counts", () => {
    const vocab = new Map(evVocabulary(fixtureEvents));
    expect(vocab.get("step")).toBe(36);
    expect(vocab.get("c2s")).toBe(55);
    expect(vocab.has("keepalive")).toBe(true);
  });
});

describe("speech labels", () => {
  test("only type-6 speech_heard produces a label, keyed by hex serial", () => {
    expect(speechLabel({ ev: "speech_heard", serial: 490, name: "Len", type: 6, text: "Len the banker" })).toEqual({
      serial: "0x000001EA",
      text: "Len the banker",
    });
    expect(speechLabel({ ev: "speech_heard", serial: 490, name: "Len", type: 0, text: "hello" })).toBeNull();
    expect(speechLabel({ ev: "speech", type: 6, text: "x" })).toBeNull();
    expect(speechLabel({ ev: "speech_heard", serial: 490, type: 6, text: "  " })).toBeNull();
  });

  test("the fixture labels Len the banker, latest label wins", () => {
    const agg = foldAll(fixtureEvents);
    expect(agg.labels["0x000001EA"]).toBe("Len the banker");
    foldEvent(agg, env(99999, "world", { ev: "speech_heard", serial: 490, type: 6, text: "Len the grumpy banker" }));
    expect(agg.labels["0x000001EA"]).toBe("Len the grumpy banker");
  });

  test("caption keeps a label that names the entity, otherwise combines", () => {
    expect(entityCaption("Len", "Len the banker")).toBe("Len the banker");
    expect(entityCaption("Len", "[Guild]")).toBe("Len ([Guild])");
    expect(entityCaption("Len", undefined)).toBe("Len");
    expect(entityCaption(undefined, undefined)).toBeNull();
  });
});

describe("traffic counters (state.traffic)", () => {
  test("163420: 54 agent walks, 1 agent speech, 54 hidden confirms, 2 re-anchors", () => {
    const tr = trafficSummary(fixture.traffic!);
    expect(tr.bySrc).toEqual({ agent: { "0x02": 54, "0xAD": 1 } });
    expect(tr.confirmsHidden).toBe(54);
    expect(tr.confirmsRewritten).toBe(0);
    expect(tr.reanchors).toBe(2);
    expect(tr.tokenEvents).toEqual({ c2s_token_stamped: 1 });
  });

  test("token-family events are collected; movement and c2s counts are not", () => {
    const tr = trafficSummary({
      proxy_events: {
        c2s_token_mismatch: 2,
        c2s_stale_token_dropped: 1,
        c2s_agent_key_cleared: 3,
        c2s_resync_seen: 4,
        step: 9,
        c2s: 5,
        s2c_confirm_rewritten: 7,
      },
      c2s: [
        ["agent", "0x02", 4],
        ["tool", "0x06", 1],
      ],
    });
    expect(tr.tokenEvents).toEqual({ c2s_token_mismatch: 2, c2s_stale_token_dropped: 1, c2s_agent_key_cleared: 3 });
    expect(tr.confirmsRewritten).toBe(7);
    expect(tr.bySrc).toEqual({ agent: { "0x02": 4 }, tool: { "0x06": 1 } });
  });
});

describe("late-joining view (event ring holds none of the session's history)", () => {
  test("the state alone still labels Len the banker and counts the 54 agent walks", () => {
    const store = new VizStore((flush) => flush());
    store.setState({ ...fixture, events: [] });
    const snap = store.getSnapshot();
    expect(snap.events).toEqual([]);
    expect(snap.agg.labels).toEqual({});

    const len = snap.state!.world.mobiles["0x000001EA"]!;
    expect(entityCaption(len.name, labelOf(snap.state!.world, snap.agg.labels, "0x000001EA"))).toBe("Len the banker");
    expect(trafficSummary(snap.state!.traffic!).bySrc.agent?.["0x02"]).toBe(54);
  });
});

describe("labelOf", () => {
  test("world.labels wins; the stream fold fills serials it lacks", () => {
    const w = cloneFixture().world;
    const stream = { "0x000001EA": "Len the grumpy banker", "0x0000BEEF": "a fresh label" };
    expect(labelOf(w, stream, "0x000001EA")).toBe("Len the banker");
    expect(labelOf(w, stream, "0x0000BEEF")).toBe("a fresh label");
    delete w.labels;
    expect(labelOf(w, stream, "0x000001EA")).toBe("Len the grumpy banker");
    expect(labelOf(null, {}, "0x000001EA")).toBeUndefined();
  });
});

describe("aggregates", () => {
  test("last agent action is paired with the world event of the same packet", () => {
    const last = foldAll(fixtureEvents).lastAgent;
    expect(last?.id).toBe("0x02");
    expect(last?.detail).toMatchObject({ ev: "walk", seq: 53, dir: 2 });
  });

  test("pairing works whichever of the two events arrives first", () => {
    const a = emptyAggregates();
    foldEvent(a, env(1, "world", { ev: "speech", text: "bank" }, 5));
    foldEvent(a, env(2, "proxy", { ev: "c2s", src: "agent", id: "0xAD" }, 5));
    expect(a.lastAgent?.detail).toMatchObject({ ev: "speech", text: "bank" });

    const b = emptyAggregates();
    foldEvent(b, env(1, "proxy", { ev: "c2s", src: "agent", id: "0xAD" }, 5));
    foldEvent(b, env(2, "world", { ev: "speech", text: "bank" }, 5));
    expect(b.lastAgent?.detail).toMatchObject({ ev: "speech", text: "bank" });

    // A client packet's world event at another time is never attributed to the agent.
    const c = emptyAggregates();
    foldEvent(c, env(1, "proxy", { ev: "c2s", src: "agent", id: "0x02" }, 5));
    foldEvent(c, env(2, "world", { ev: "walk", dir: 4 }, 6));
    expect(c.lastAgent?.detail).toBeNull();
  });

  test("trail follows step `to` positions and is capped", () => {
    const agg = foldAll(fixtureEvents);
    expect(agg.trail.length).toBe(36);
    expect(agg.trail.at(-1)).toEqual([1963, 2597]);
    expect(agg.live.steps.length).toBe(36);

    for (let i = 0; i < TRAIL_LEN + 10; i++) {
      foldEvent(agg, env(10000 + i, "proxy", { ev: "step", from: [i, 0], to: [i + 1, 0], z: 0 }));
    }
    expect(agg.trail.length).toBe(TRAIL_LEN);
    expect(agg.trail.at(-1)).toEqual([TRAIL_LEN + 10, 0]);
  });

  test("movement log keeps proxy movement decisions only", () => {
    const agg = foldAll(fixtureEvents);
    const evs = new Set(agg.movementLog.map((e) => e.data.ev));
    expect(evs.has("step")).toBe(true);
    expect(evs.has("reanchor_client")).toBe(true);
    expect(evs.has("c2s")).toBe(false);
    expect(evs.has("s2c_confirm_hidden")).toBe(false);
  });
});

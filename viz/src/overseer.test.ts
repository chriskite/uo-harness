import { describe, expect, test } from "bun:test";
import {
  EMPTY_OVERSEER,
  chatError,
  fmtAgo,
  mergeOverseer,
  overseerStatus,
  thoughtPreview,
  timeline,
  type ChatRow,
  type Juncture,
  type OverseerResponse,
} from "./overseer.ts";

const chat = (id: number, t: number, role: ChatRow["role"] = "user", kind: ChatRow["kind"] = "message"): ChatRow => ({
  id,
  t,
  role,
  kind,
  text: `m${id}`,
  data: {},
});
const junc = (id: number, t: number, severity: Juncture["severity"] = "attention"): Juncture => ({
  id,
  t,
  source: "lumber",
  kind: "stuck",
  severity,
  summary: `j${id}`,
  data: {},
  acked_t: null,
});
const resp = (p: Partial<OverseerResponse>): OverseerResponse => ({
  chat: [],
  junctures: [],
  open: 0,
  open_ids: [],
  heartbeat: null,
  now: 1000,
  store: true,
  ...p,
});

describe("mergeOverseer", () => {
  test("appends new rows, advances cursors, drops overlap duplicates", () => {
    let s = mergeOverseer(EMPTY_OVERSEER, resp({ chat: [chat(1, 10), chat(2, 11)], junctures: [junc(1, 5)], open_ids: [1] }));
    expect([s.afterChat, s.afterJuncture]).toEqual([2, 1]);
    s = mergeOverseer(s, resp({ chat: [chat(2, 11), chat(3, 12)], open_ids: [] }));
    expect(s.chat.map((c) => c.id)).toEqual([1, 2, 3]);
    expect(s.afterChat).toBe(3);
    expect(s.afterJuncture).toBe(1);
    expect(s.openIds).toEqual([]);
  });
  test("an empty poll keeps the arrays (no re-render churn) and the cursors", () => {
    const s = mergeOverseer(EMPTY_OVERSEER, resp({ chat: [chat(4, 1)] }));
    const s2 = mergeOverseer(s, resp({ heartbeat: 999 }));
    expect(s2.chat).toBe(s.chat);
    expect(s2.afterChat).toBe(4);
    expect(s2.heartbeat).toBe(999);
  });
});

describe("timeline", () => {
  const s = mergeOverseer(
    EMPTY_OVERSEER,
    resp({
      chat: [chat(1, 10), chat(2, 30, "overseer", "thought"), chat(3, 20, "overseer")],
      junctures: [junc(1, 20, "urgent"), junc(2, 25)],
      open_ids: [1],
    }),
  );
  test("time order; chat before a juncture at the same t; acked junctures hidden", () => {
    expect(timeline(s).map((i) => i.key)).toEqual(["c1", "c3", "j1", "c2"]);
  });
  test("showAcked brings acked junctures back, marked not open", () => {
    const items = timeline(s, true);
    expect(items.map((i) => i.key)).toEqual(["c1", "c3", "j1", "j2", "c2"]);
    const j2 = items.find((i) => i.key === "j2");
    expect(j2?.type === "juncture" && j2.open).toBe(false);
  });
  test("empty", () => {
    expect(timeline(EMPTY_OVERSEER)).toEqual([]);
  });
});

describe("overseerStatus / fmtAgo", () => {
  test("fresh heartbeat (< 90 s) is active; stale and never are not", () => {
    expect(overseerStatus(1000, 1089.9)).toEqual({ active: true, text: "overseer active" });
    expect(overseerStatus(1000, 1090)).toEqual({ active: false, text: "no overseer running (last seen 1m ago)" });
    expect(overseerStatus(null, 5)).toEqual({ active: false, text: "no overseer running (never seen)" });
  });
  test("ages", () => {
    expect(fmtAgo(-2)).toBe("0s");
    expect(fmtAgo(59.9)).toBe("59s");
    expect(fmtAgo(3599)).toBe("59m");
    expect(fmtAgo(3600 + 5 * 60)).toBe("1h 05m");
    expect(fmtAgo(2 * 86400 + 4 * 3600)).toBe("2d 4h");
  });
});

describe("chatError / thoughtPreview", () => {
  test("same rule as POST /api/chat: 1..2000 chars after trimming", () => {
    expect(chatError("")).toBe("empty");
    expect(chatError("   \n")).toBe("empty");
    expect(chatError("hi")).toBeNull();
    expect(chatError(`  ${"x".repeat(2000)}  `)).toBeNull();
    expect(chatError("x".repeat(2001))).toBe("1 characters over 2000");
  });
  test("first line, cut with an ellipsis", () => {
    expect(thoughtPreview("  plan: go north\nthen chop")).toBe("plan: go north");
    expect(thoughtPreview("abcdefghij", 5)).toBe("abcd…");
  });
});

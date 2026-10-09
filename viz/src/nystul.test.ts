import { describe, expect, test } from "bun:test";
import {
  NYSTUL_MAX_CHARS,
  askError,
  isRunning,
  proposalView,
  runSummary,
  stepLabel,
  type NystulConversation,
  type NystulMessage,
  type NystulProposal,
  type ProposalEntry,
} from "./nystul.ts";

const msg = (over: Partial<NystulMessage>): NystulMessage => ({
  id: 2,
  role: "nystul",
  t: 1_700_000_000,
  text: "",
  status: "done",
  steps: [],
  model: null,
  cost: null,
  tokens_in: null,
  tokens_out: null,
  seconds: null,
  error: null,
  proposals: [],
  ...over,
});

describe("askError", () => {
  test("empty and whitespace-only are refused", () => {
    expect(askError("")).toBe("empty");
    expect(askError("  \n\t ")).toBe("empty");
  });

  test(`${NYSTUL_MAX_CHARS} characters pass, one more is refused`, () => {
    expect(NYSTUL_MAX_CHARS).toBe(4000);
    expect(askError("x".repeat(4000))).toBeNull();
    expect(askError(` ${"x".repeat(4000)} `)).toBeNull();
    expect(askError("x".repeat(4001))).toBe("1 characters over 4000");
  });
});

describe("stepLabel", () => {
  test("a known tool gets its verb and the first argument", () => {
    expect(stepLabel({ tool: "uo_api", args: { route: "/api/state", path: "world.self" }, t: 0, ok: true })).toBe(
      "gazed into the scrying pool: /api/state",
    );
    expect(stepLabel({ tool: "uo_ctl", args: { command: "runes", args: ["libraries"] }, t: 0, ok: null })).toBe(
      "consulted the Seer's instruments: runes",
    );
  });

  test("an unknown tool shows its name; long arguments are capped at 60", () => {
    expect(stepLabel({ tool: "uo_mystery", args: {}, t: 0, ok: false })).toBe("uo_mystery");
    const label = stepLabel({ tool: "uo_sql", args: { db: "harness", query: "q".repeat(100) }, t: 0, ok: true });
    expect(label).toBe(`searched the archives: ${"q".repeat(59)}…`);
  });
});

describe("runSummary", () => {
  test("all parts", () => {
    const steps = Array.from({ length: 5 }, () => ({ tool: "uo_read", args: {}, t: 0, ok: true }));
    expect(runSummary(msg({ seconds: 12.4, steps, cost: 0.0412 }))).toBe("12 s · 5 lookups · $0.04");
  });

  test("null parts are dropped", () => {
    expect(runSummary(msg({}))).toBe("0 lookups");
    expect(runSummary(msg({ seconds: 3, steps: [{ tool: "uo_list", args: {}, t: 0, ok: true }] }))).toBe("3 s · 1 lookup");
  });
});

describe("isRunning", () => {
  const conv = (messages: NystulMessage[]): NystulConversation => ({
    conversation: { id: 1, title: "t", t_created: 0, t_updated: 0 },
    messages,
  });

  test("any running message makes the conversation running", () => {
    expect(isRunning(null)).toBe(false);
    expect(isRunning(conv([msg({ role: "user" }), msg({})]))).toBe(false);
    expect(isRunning(conv([msg({ role: "user" }), msg({ status: "running" })]))).toBe(true);
  });
});

describe("proposalView", () => {
  const entry: ProposalEntry = {
    id: 12,
    kind: "fact",
    topic: "five flamehound team",
    content: "A summoner team",
    confidence: 0.6,
    importance: 4,
    status: "active",
    source_type: "community",
    source_ref: null,
    tags: ["discord"],
  };
  const prop = (params: Record<string, unknown>, over: Partial<NystulProposal> = {}): NystulProposal => ({
    id: 1,
    t: 0,
    params,
    before: {},
    status: "pending",
    t_decided: null,
    result: null,
    error: null,
    ...over,
  });

  test("an update shows the entry it replaces and only the fields it sets, in order", () => {
    const v = proposalView(
      prop({ op: "update", id: 12, why: "tamer, not summoner", content: "A tamer team", importance: 5, tags: [] }, { before: { "12": entry } }),
    );
    expect(v.title).toBe("Update #12");
    expect(v.why).toBe("tamer, not summoner");
    expect(v.before?.content).toBe("A summoner team");
    expect(v.fields).toEqual([
      { label: "content", value: "A tamer team" },
      { label: "importance", value: "5" },
    ]);
    expect(v.outcome).toBeNull();
  });

  test("an add names its kind in the title, not as a field; a superseded entry is its before", () => {
    const v = proposalView(
      prop(
        { op: "add", kind: "fact", topic: "t", content: "c", tags: ["a", "b"], confidence: 0.9, supersedes: 12, why: "w" },
        { before: { "12": entry } },
      ),
    );
    expect(v.title).toBe("Add a fact");
    expect(v.fields.map((f) => f.label)).toEqual(["topic", "content", "tags", "confidence", "replaces"]);
    expect(v.fields.find((f) => f.label === "tags")?.value).toBe("a, b");
    expect(v.fields.find((f) => f.label === "confidence")?.value).toBe("0.90");
    expect(v.before?.id).toBe(12);
  });

  test("a retract has no fields; decided proposals say how they ended instead of offering Approve", () => {
    expect(proposalView(prop({ op: "retract", id: 12, why: "wrong" })).fields).toEqual([]);
    expect(proposalView(prop({ op: "add", kind: "fact", why: "w" }, { status: "applied", result: { id: 99, action: "added" } })).outcome).toBe(
      "approved: added as #99",
    );
    expect(proposalView(prop({ op: "retract", id: 12, why: "w" }, { status: "failed", error: "#12 isn't an active entry" })).outcome).toBe(
      "failed: #12 isn't an active entry",
    );
    expect(proposalView(prop({ op: "retract", id: 12, why: "w" }, { status: "applying" })).outcome).toBe("applying…");
  });
});

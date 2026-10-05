import { describe, expect, test } from "bun:test";
import { supersede, type SseMessage } from "./sse.ts";

const st = (n: number): SseMessage => ({ kind: "state", data: `s${n}` });
const ev = (n: number): SseMessage => ({ kind: "events", data: `e${n}` });

describe("supersede", () => {
  test("only the newest state survives; every events batch keeps its place", () => {
    expect(supersede([ev(1), st(1), ev(2), st(2), ev(3), st(3)])).toEqual([ev(1), ev(2), ev(3), st(3)]);
    expect(supersede([st(1), ev(1), st(2), ev(2)])).toEqual([ev(1), st(2), ev(2)]);
  });

  test("nothing to drop: a lone state, events only, empty", () => {
    expect(supersede([ev(1), st(1)])).toEqual([ev(1), st(1)]);
    expect(supersede([ev(1), ev(2)])).toEqual([ev(1), ev(2)]);
    expect(supersede([])).toEqual([]);
  });
});

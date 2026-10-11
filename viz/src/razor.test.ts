import { describe, expect, test } from "bun:test";
import {
  type Action,
  assign,
  type Binding,
  buildTree,
  comboFromKey,
  comboFromMouseButton,
  comboFromWheel,
  diffCount,
  formatCombo,
  offBoard,
  TKL_ROWS,
} from "./razor.ts";

const b = (name: string, mod: number, key: number, send = false): Binding => ({ name, mod, key, send });

describe("combos", () => {
  test("comboFromKey", () => {
    expect(comboFromKey({ code: "KeyF", ctrlKey: true })).toEqual({ mod: 2, key: 70 });
    expect(comboFromKey({ code: "Backquote" })?.key).toBe(192);
    expect(comboFromKey({ code: "ShiftLeft", shiftKey: true })).toBeNull();
    expect(comboFromKey({ code: "MetaLeft" })).toBeNull();
  });
  test("mouse", () => {
    expect(comboFromMouseButton(1, 0)?.key).toBe(-3);
    expect(comboFromMouseButton(3, 0)?.key).toBe(-4);
    expect(comboFromMouseButton(0, 0)).toBeNull();
    expect(comboFromWheel(-5, 4)).toEqual({ mod: 4, key: -1 });
  });
  test("formatCombo", () => {
    expect(formatCombo(7, 70)).toBe("Ctrl+Alt+Shift+F");
    expect(formatCombo(0, -3)).toBe("Middle Button");
  });
});

describe("assign", () => {
  test("moving a bound action keeps its index and send", () => {
    const out = assign([b("a", 0, 65), b("x", 0, 66, true), b("c", 0, 67)], "x", 2, 70);
    expect(out[1]).toEqual(b("x", 2, 70, true));
    expect(out.length).toBe(3);
  });
  test("taking a held combo removes the holder and takes its index", () => {
    const out = assign([b("a", 0, 65), b("h", 0, 66), b("c", 0, 67)], "n", 0, 66);
    expect(out.map((x) => x.name)).toEqual(["a", "n", "c"]);
    expect(out[1]?.send).toBe(false);
  });
  test("mouse forces send false", () => {
    const out = assign([b("x", 0, 65, true)], "x", 0, -3);
    expect(out[0]?.send).toBe(false);
  });
});

test("diffCount counts added, removed and changed", () => {
  const a = [b("same", 0, 65), b("changed", 0, 66), b("removed", 0, 67)];
  const c = [b("same", 0, 65), b("changed", 0, 66, true), b("added", 0, 68)];
  expect(diffCount(a, c)).toBe(3);
  expect(diffCount(a, a)).toBe(0);
});

test("buildTree preserves first-seen category order", () => {
  const act = (name: string, path: string[]): Action => ({ name, label: name, path, confirmed: true });
  const t = buildTree([act("1", ["Targets"]), act("2", ["Items", "Use"]), act("3", ["Targets"]), act("4", [])]);
  expect(t.children.map((c) => c.label)).toEqual(["Targets", "Items"]);
  expect(t.children[0]?.actions.length).toBe(2);
  expect(t.actions.map((a) => a.name)).toEqual(["4"]);
});

test("offBoard returns numpad bindings only", () => {
  const out = offBoard([b("a", 0, 65), b("n", 0, 97), b("m", 0, -1), b("s", 0, 107)]);
  expect(out.map((x) => x.name)).toEqual(["n", "s"]);
});

test("TKL caps are unique", () => {
  const keys = TKL_ROWS.flat().flatMap((c) => (c.key === null ? [] : [c.key]));
  expect(new Set(keys).size).toBe(keys.length);
});

import { describe, expect, test } from "bun:test";
import { cellOutline, groveQuery, treeAt, treeText, type GroveResponse, type GroveTree } from "./grove.ts";
import type { AgentIntent } from "./types.ts";

const intent = (spot?: string): AgentIntent => ({ text: "chop", kind: "chop", loop: "lumber", since: 0, ...(spot ? { spot } : {}) });
const tree = (extra: Partial<GroveTree>): GroveTree => ({
  x: 10, y: 20, z: 0, graphic: "0x0CD6", name: "cedar tree", seed: false, kind: "tree", inside: true, state: "ready", ...extra,
});

describe("groveQuery", () => {
  test("the intent's spot wins; else the spot around the true position; else nothing", () => {
    expect(groveQuery(intent("witcher 23"), [1, 2], 0)).toBe("spot=witcher%2023");
    expect(groveQuery(intent(), [1694, 672], 1)).toBe("facet=1&x=1694&y=672");
    expect(groveQuery(intent(), [1694, 672], null)).toBe("facet=0&x=1694&y=672");
    expect(groveQuery(intent(), null, 0)).toBeNull();
    expect(groveQuery(null, [1, 2], 0)).toBeNull();
  });
});

describe("cellOutline", () => {
  test("an L of three cells: only the edges that don't face another cell, in tile corners", () => {
    const segs = cellOutline([[10, 10], [11, 10], [10, 11]], 8);
    expect(segs).toHaveLength(8);
    expect(segs).not.toContainEqual([88, 80, 88, 88]); // between (10,10) and (11,10)
    expect(segs).not.toContainEqual([80, 88, 88, 88]); // between (10,10) and (10,11)
    expect(segs).toContainEqual([96, 80, 96, 88]); // (11,10)'s east edge
    expect(segs).toContainEqual([80, 96, 88, 96]); // (10,11)'s south edge
  });
});

describe("treeAt / treeText", () => {
  test("the tree on a tile", () => {
    const g = { spot: null, store: true, trees: [tree({}), tree({ x: 11 })] } as GroveResponse;
    expect(treeAt(g, 11, 20)?.x).toBe(11);
    expect(treeAt(g, 12, 20)).toBeNull();
    expect(treeAt(null, 10, 20)).toBeNull();
  });
  test("states read with their times against now", () => {
    expect(treeText(tree({}), 1000)).toBe("cedar tree 0x0CD6: choppable");
    expect(treeText(tree({ state: "depleted", since: 400, until: 4300 }), 1000)).toBe(
      "cedar tree 0x0CD6: depleted 10m ago, ready in 55m",
    );
    expect(treeText(tree({ state: "not_tree", inside: false }), 0)).toBe(
      "cedar tree 0x0CD6: the server says it isn't a tree · outside the area: the runner doesn't try it",
    );
    expect(treeText(tree({ kind: "excluded", why: "passable", name: "snow tree", graphic: "0x53B0", state: undefined }), 0)).toBe(
      "snow tree 0x53B0: not counted: passable art (not a trunk)",
    );
  });
});
